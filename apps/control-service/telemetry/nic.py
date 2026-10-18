"""NIC / IPv4 candidate selection for MONITORING_IP.

Default exclusions: loopback, docker0/br-, Linux veth*, WSL/Default Switch
vEthernet. On WSL2 NAT, also prefer Windows-host NICs and drop distro eth0
172.16/12 addresses (never use WSL NAT IP as MONITORING_IP).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Iterable, Sequence

# Linux veth pairs: case-sensitive so Windows "vEthernet (...)" is not matched.
_LINUX_VETH = re.compile(r"^veth")

# Name patterns excluded by default (loopback / docker / WSL internal switches).
_DEFAULT_NAME_EXCLUDE = (
    re.compile(r"^lo$", re.I),
    re.compile(r"^docker\d*$", re.I),
    re.compile(r"^br-", re.I),
    re.compile(r"^vEthernet \(WSL", re.I),
    re.compile(r"^vEthernet \(Default Switch\)$", re.I),
    # Internal Hyper-V / WSL firewall adapters — not external LAN switches.
    re.compile(r"^vEthernet \(.*Hyper-V.*\)$", re.I),
    re.compile(r"^WSL$", re.I),
)

_RFC1918_172 = ipaddress.ip_network("172.16.0.0/12")


@dataclass(frozen=True)
class NicCandidate:
    name: str
    ip: str
    prefix: int | None = None

    @property
    def cidr(self) -> str | None:
        if self.prefix is None:
            return None
        return f"{self.ip}/{self.prefix}"


def _is_loopback_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return True


def _is_link_local(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_link_local
    except ValueError:
        return True


def _in_172_16(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _RFC1918_172
    except ValueError:
        return False


def in_wsl() -> bool:
    """True when running inside a WSL distro."""
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return False
    return "microsoft" in text.lower() or "wsl" in text.lower()


def name_excluded(
    name: str,
    *,
    extra_patterns: Sequence[re.Pattern[str]] | None = None,
) -> bool:
    """True if interface name matches default (or extra) exclusion patterns."""
    if _LINUX_VETH.match(name):
        return True
    patterns = list(_DEFAULT_NAME_EXCLUDE)
    if extra_patterns:
        patterns.extend(extra_patterns)
    return any(p.search(name) for p in patterns)


def is_wsl_nat_iface(candidate: NicCandidate) -> bool:
    """WSL distro NAT address (e.g. eth0 172.28.x) — never MONITORING_IP on NAT path."""
    # Typical WSL2 NAT NIC names inside the distro.
    if candidate.name.lower() not in ("eth0", "eth1", "ens33", "enp0s3"):
        # Still treat unnamed 172.16/12 on eth* as NAT when clearly WSL-ish.
        if not candidate.name.lower().startswith("eth"):
            return False
    return _in_172_16(candidate.ip)


def filter_nic_candidates(
    candidates: Iterable[NicCandidate],
    *,
    exclude_docker_wsl: bool = True,
    exclude_loopback: bool = True,
    exclude_link_local: bool = True,
    exclude_wsl_nat: bool = False,
    extra_name_patterns: Sequence[re.Pattern[str]] | None = None,
) -> list[NicCandidate]:
    """Filter IPv4 NIC candidates for MONITORING_IP selection.

    ``exclude_wsl_nat`` drops distro eth* addresses in 172.16.0.0/12 so WSL NAT
    IPs are never offered as MONITORING_IP. Do not enable on bare-metal Linux
    (corporate 172.16/12 LANs on eth0 must remain selectable).
    """
    out: list[NicCandidate] = []
    for c in candidates:
        if exclude_loopback and _is_loopback_ip(c.ip):
            continue
        if exclude_link_local and _is_link_local(c.ip):
            continue
        if exclude_docker_wsl and name_excluded(
            c.name, extra_patterns=extra_name_patterns
        ):
            continue
        if exclude_wsl_nat and is_wsl_nat_iface(c):
            continue
        out.append(c)
    return out


def _parse_ip_json(payload: str) -> list[NicCandidate]:
    data = json.loads(payload)
    found: list[NicCandidate] = []
    for iface in data:
        name = str(iface.get("ifname") or iface.get("name") or "")
        for addr_info in iface.get("addr_info") or []:
            family = addr_info.get("family")
            if family is not None and family != "inet":
                continue
            local = addr_info.get("local")
            if not local:
                continue
            try:
                if ipaddress.ip_address(local).version != 4:
                    continue
            except ValueError:
                continue
            prefix = addr_info.get("prefixlen")
            found.append(
                NicCandidate(
                    name=name,
                    ip=str(local),
                    prefix=int(prefix) if prefix is not None else None,
                )
            )
    return found


def _parse_ip_dash_o(text: str) -> list[NicCandidate]:
    # Example: ``2: eth0    inet 192.168.1.10/24 brd ... scope global eth0``
    found: list[NicCandidate] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        name = parts[1].rstrip(":")
        cidr = parts[3]
        try:
            iface = ipaddress.ip_interface(cidr)
        except ValueError:
            continue
        if iface.version != 4:
            continue
        found.append(
            NicCandidate(name=name, ip=str(iface.ip), prefix=iface.network.prefixlen)
        )
    return found


def _parse_windows_netipaddress(text: str) -> list[NicCandidate]:
    """Parse ``Get-NetIPAddress -AddressFamily IPv4`` table or CSV-ish lines.

    Accepts lines like: ``Ethernet;192.168.1.10;24`` (InterfaceAlias;IP;Prefix)
    or PowerShell Format-Table rows with Alias and IPAddress columns.
    """
    found: list[NicCandidate] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("interfacealias"):
            continue
        if ";" in line:
            parts = [p.strip() for p in line.split(";")]
            if len(parts) < 2:
                continue
            name, ip = parts[0], parts[1]
            prefix = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        else:
            # Whitespace-separated: Alias ... IPAddress PrefixLength
            parts = line.split()
            if len(parts) < 2:
                continue
            # Heuristic: last token that looks like IPv4 is the address;
            # first token(s) before it form the alias (may contain spaces — weak).
            ip = None
            prefix = None
            ip_idx = None
            for i, tok in enumerate(parts):
                try:
                    if ipaddress.ip_address(tok).version == 4:
                        ip = tok
                        ip_idx = i
                        break
                except ValueError:
                    continue
            if ip is None or ip_idx is None:
                continue
            name = " ".join(parts[:ip_idx]) or parts[0]
            if ip_idx + 1 < len(parts) and parts[ip_idx + 1].isdigit():
                prefix = int(parts[ip_idx + 1])
        try:
            if ipaddress.ip_address(ip).version != 4:
                continue
        except ValueError:
            continue
        found.append(NicCandidate(name=name, ip=ip, prefix=prefix))
    return found


def _collect_windows_host_candidates() -> list[NicCandidate]:
    """Enumerate Windows host IPv4 via PowerShell (native .exe or from WSL)."""
    # Semicolon CSV keeps aliases with spaces intact.
    ps = (
        "Get-NetIPAddress -AddressFamily IPv4 | "
        "Where-Object { $_.IPAddress -and $_.InterfaceAlias } | "
        "ForEach-Object { '{0};{1};{2}' -f $_.InterfaceAlias, $_.IPAddress, $_.PrefixLength }"
    )
    for exe in ("powershell.exe", "pwsh.exe"):
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-Command", ps],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        try:
            parsed = _parse_windows_netipaddress(proc.stdout)
        except (ValueError, TypeError):
            continue
        if parsed:
            return parsed
    return []


def _collect_linux_candidates() -> list[NicCandidate]:
    for args, parser in (
        (["ip", "-j", "-4", "addr"], _parse_ip_json),
        (["ip", "-4", "-o", "addr"], _parse_ip_dash_o),
    ):
        try:
            proc = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        try:
            return parser(proc.stdout)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            continue
    return []


def list_ipv4_candidates(
    *,
    exclude_docker_wsl: bool = True,
    apply_defaults: bool = True,
    windows_net_mode: str | None = None,
) -> list[NicCandidate]:
    """Enumerate IPv4 addresses for MONITORING_IP selection.

    Native Windows ``.exe`` (``os.name == "nt"``, not inside WSL) always uses
    PowerShell host NICs. WSL is not required.

    ``windows_net_mode`` (legacy / in-WSL only):
      - ``"nat"``: prefer Windows host NICs via powershell.exe; drop WSL eth*
        172.16/12 addresses.
      - ``"mirrored"``: Linux ``ip`` inside WSL already sees LAN addresses.
      - ``None``: auto — if inside WSL, treat as ``nat``.
    """
    # Packaged Windows app: never require WSL or the Linux ``ip`` tool.
    if os.name == "nt" and not in_wsl():
        raw = _collect_windows_host_candidates()
        if not apply_defaults:
            return raw
        return filter_nic_candidates(
            raw,
            exclude_docker_wsl=exclude_docker_wsl,
            exclude_wsl_nat=False,
        )

    # Bare Linux (AppImage / source): always use ``ip``. Ignore leftover
    # windows_net_mode from settings so we never spawn PowerShell.
    if not in_wsl():
        raw = _collect_linux_candidates()
        if not apply_defaults:
            return raw
        return filter_nic_candidates(
            raw,
            exclude_docker_wsl=exclude_docker_wsl,
            exclude_wsl_nat=False,
        )

    mode = windows_net_mode
    if mode is None:
        mode = "nat"

    exclude_wsl_nat = mode == "nat"
    raw: list[NicCandidate] = []

    if mode == "nat":
        raw = _collect_windows_host_candidates()
        if not raw:
            # Fallback: Linux view, but WSL-NAT filter still applied below.
            raw = _collect_linux_candidates()
    else:
        raw = _collect_linux_candidates()

    if not apply_defaults:
        return raw
    return filter_nic_candidates(
        raw,
        exclude_docker_wsl=exclude_docker_wsl,
        exclude_wsl_nat=exclude_wsl_nat,
    )


def pick_default_monitoring_ip(
    candidates: Sequence[NicCandidate],
    *,
    gateway_ip: str | None = None,
) -> NicCandidate | None:
    """Prefer NIC sharing a subnet with gateway_ip; else first filtered candidate."""
    if not candidates:
        return None
    if gateway_ip:
        try:
            gw = ipaddress.ip_address(gateway_ip)
        except ValueError:
            gw = None
        if gw is not None:
            for c in candidates:
                if c.prefix is None:
                    continue
                try:
                    net = ipaddress.ip_network(f"{c.ip}/{c.prefix}", strict=False)
                except ValueError:
                    continue
                if gw in net:
                    return c
    return candidates[0]
