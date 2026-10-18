"""LAN discovery: find hosts with an open SSH port on the monitoring subnet."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import socket
from typing import Any

from .paths import load_settings

logger = logging.getLogger(__name__)

DEFAULT_SSH_PORT = 22
DEFAULT_TIMEOUT_S = 0.35
MAX_HOSTS = 256
MAX_WORKERS = 64


def _probe_tcp(ip: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _network_for_scan(
    monitoring_ip: str,
    *,
    prefix: int | None,
) -> ipaddress.IPv4Network:
    """Pick a scan range. Cap large prefixes at /24 around the monitoring IP."""
    host = ipaddress.ip_address(monitoring_ip)
    if not isinstance(host, ipaddress.IPv4Address):
        raise ValueError("Only IPv4 discovery is supported")
    pref = 24 if prefix is None else int(prefix)
    if pref < 24:
        # Avoid scanning huge ranges (/16 etc.).
        pref = 24
    if pref > 30:
        pref = 30
    return ipaddress.ip_network(f"{host}/{pref}", strict=False)


def scan_ssh_hosts(
    *,
    monitoring_ip: str,
    prefix: int | None = None,
    port: int = DEFAULT_SSH_PORT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    exclude_ips: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Return hosts on the LAN that accept TCP connections on ``port`` (SSH)."""
    net = _network_for_scan(monitoring_ip, prefix=prefix)
    skip = set(exclude_ips or ())
    skip.add(monitoring_ip)
    hosts: list[str] = []
    for addr in net.hosts():
        ip = str(addr)
        if ip in skip:
            continue
        hosts.append(ip)
        if len(hosts) >= MAX_HOSTS:
            break

    found: list[dict[str, Any]] = []
    workers = min(MAX_WORKERS, max(4, len(hosts)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_probe_tcp, ip, port, timeout_s): ip for ip in hosts
        }
        for fut in concurrent.futures.as_completed(futs):
            ip = futs[fut]
            try:
                if fut.result():
                    found.append({"ip": ip, "port": port, "open": True})
            except Exception:  # noqa: BLE001
                logger.debug("probe failed for %s", ip, exc_info=True)

    found.sort(key=lambda row: tuple(int(p) for p in row["ip"].split(".")))
    return found


def discover_gateways(
    *,
    monitoring_ip: str | None = None,
    prefix: int | None = None,
    port: int = DEFAULT_SSH_PORT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Discover SSH-reachable hosts using settings / NIC context."""
    settings = load_settings()
    mon = (monitoring_ip or str(settings.get("monitoring_ip") or "")).strip()
    if not mon:
        raise ValueError(
            "monitoring_ip required — save this PC LAN address first "
            "(Gateways → Registration or first-run Network step)."
        )

    pref = prefix
    if pref is None:
        # Prefer prefix from the matching NIC candidate when available.
        try:
            from telemetry.nic import list_ipv4_candidates  # type: ignore[import-not-found]

            for c in list_ipv4_candidates():
                if c.ip == mon and c.prefix is not None:
                    pref = int(c.prefix)
                    break
        except Exception:  # noqa: BLE001
            pref = None

    hosts = scan_ssh_hosts(
        monitoring_ip=mon,
        prefix=pref,
        port=port,
        timeout_s=timeout_s,
        exclude_ips={mon},
    )
    return {
        "ok": True,
        "monitoring_ip": mon,
        "prefix": pref if pref is not None else 24,
        "port": port,
        "scanned_hint": f"TCP {port} on subnet of {mon}",
        "hosts": hosts,
        "count": len(hosts),
    }
