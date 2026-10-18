"""Guided Windows firewall checklist (K18).

Primary path (packaged Windows .exe): Loki listens on ``0.0.0.0:3100`` as a
native process — only a Windows Firewall allow rule is needed (no WSL, no
portproxy).

Legacy helper ``generate_wsl2_nat_checklist`` remains for older WSL2 NAT labs.

Display-only: generate exact ``netsh`` commands for the user to run elevated.
Never execute or silently apply firewall/portproxy rules in v1.
"""

from __future__ import annotations

from dataclasses import dataclass


LOKI_PORT = 3100
FIREWALL_RULE_NAME = "IoT Gateway Loki 3100"


@dataclass(frozen=True)
class ChecklistItem:
    """One copyable step shown in the Setup Wizard."""

    id: str
    title: str
    command: str
    notes: str = ""


def _firewall_add(listen_port: int = LOKI_PORT) -> str:
    return (
        f'netsh advfirewall firewall add rule name="{FIREWALL_RULE_NAME}" '
        f"dir=in action=allow protocol=TCP localport={listen_port}"
    )


def _firewall_delete() -> str:
    return f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE_NAME}"'


def _firewall_show() -> str:
    return f'netsh advfirewall firewall show rule name="{FIREWALL_RULE_NAME}"'


def generate_windows_native_checklist(
    *,
    monitoring_ip: str | None = None,
    listen_port: int = LOKI_PORT,
    include_firewall_delete: bool = False,
) -> list[ChecklistItem]:
    """Firewall-only checklist for native Windows Loki (no WSL / portproxy)."""
    verify_note_parts = [
        "Run in an elevated PowerShell or CMD. Loki already listens on this PC.",
        "Do not add portproxy — that is only for legacy WSL2 setups.",
    ]
    if monitoring_ip:
        from .readiness import gateway_loki_ready_curl

        verify_note_parts.append(
            "After confirm, probe from the gateway (SSH): "
            + gateway_loki_ready_curl(monitoring_ip, port=listen_port)
        )

    items: list[ChecklistItem] = []
    if include_firewall_delete:
        items.append(
            ChecklistItem(
                id="firewall_delete",
                title="Remove existing firewall rule (idempotent re-run)",
                command=_firewall_delete(),
                notes="Safe if the rule is missing; avoids duplicate-rule errors on add.",
            )
        )

    items.extend(
        [
            ChecklistItem(
                id="firewall_allow",
                title="Allow inbound TCP 3100 (Windows Firewall)",
                command=_firewall_add(listen_port),
                notes="Inbound allow so the gateway can push logs to MONITORING_IP:3100.",
            ),
            ChecklistItem(
                id="firewall_show",
                title="Verify firewall rule",
                command=_firewall_show(),
                notes="Confirm the rule exists and is Enabled.",
            ),
            ChecklistItem(
                id="user_confirm",
                title="Confirm in Setup Wizard",
                command="",
                notes=" ".join(verify_note_parts),
            ),
        ]
    )
    return items


def generate_wsl2_nat_checklist(
    *,
    wsl2_ip: str,
    monitoring_ip: str | None = None,
    listen_address: str = "0.0.0.0",
    listen_port: int = LOKI_PORT,
    connect_port: int = LOKI_PORT,
    include_firewall_delete: bool = False,
) -> list[ChecklistItem]:
    """Build the K18 guided checklist for Loki :3100 only.

    ``monitoring_ip`` is the Windows LAN IP Promtail should use; included in
    notes so the wizard can show verify steps. Commands are never executed here.
    """
    wsl2_ip = wsl2_ip.strip()
    if not wsl2_ip:
        raise ValueError("wsl2_ip is required")

    portproxy = (
        f"netsh interface portproxy add v4tov4 "
        f"listenaddress={listen_address} listenport={listen_port} "
        f"connectaddress={wsl2_ip} connectport={connect_port}"
    )
    portproxy_multiline = (
        f"netsh interface portproxy add v4tov4 "
        f"listenaddress={listen_address} listenport={listen_port} ^\n"
        f"  connectaddress={wsl2_ip} connectport={connect_port}"
    )
    show_proxy = "netsh interface portproxy show v4tov4"
    verify_note_parts = [
        "Run in an elevated PowerShell or CMD. Do not apply these for :9137/:8501.",
        f"WSL2 connect address: {wsl2_ip}.",
    ]
    if monitoring_ip:
        from .readiness import gateway_loki_ready_curl

        verify_note_parts.append(
            "After confirm, probe from the gateway (SSH): "
            + gateway_loki_ready_curl(monitoring_ip, port=listen_port)
        )

    items: list[ChecklistItem] = []
    if include_firewall_delete:
        items.append(
            ChecklistItem(
                id="firewall_delete",
                title="Remove existing firewall rule (idempotent re-run)",
                command=_firewall_delete(),
                notes="Safe if the rule is missing; avoids duplicate-rule errors on add.",
            )
        )

    items.extend(
        [
            ChecklistItem(
                id="portproxy_add",
                title="Add portproxy (LAN :3100 → WSL2 Loki)",
                command=portproxy,
                notes=(
                    "Maps Windows listen → WSL2 Loki only. "
                    f"Multiline CMD form:\n{portproxy_multiline}"
                ),
            ),
            ChecklistItem(
                id="firewall_allow",
                title="Allow inbound TCP 3100 (Windows Firewall)",
                command=_firewall_add(listen_port),
                notes="Inbound allow for Promtail push from the gateway subnet.",
            ),
            ChecklistItem(
                id="portproxy_show",
                title="Verify portproxy table",
                command=show_proxy,
                notes="Confirm listenport 3100 → connectaddress matches current WSL2 IP.",
            ),
            ChecklistItem(
                id="user_confirm",
                title="Confirm in Setup Wizard",
                command="",
                notes=" ".join(verify_note_parts),
            ),
        ]
    )
    return items


def format_checklist_for_display(
    items: list[ChecklistItem],
    *,
    title: str | None = None,
) -> str:
    """Human-readable block for copy/paste UI / logs."""
    header = title or (
        "=== Windows — Loki :3100 firewall checklist (K18) ==="
        if any(i.id == "firewall_show" for i in items)
        and not any(i.id.startswith("portproxy") for i in items)
        else "=== Windows WSL2 NAT — Loki :3100 guided checklist (K18) ==="
    )
    lines: list[str] = [
        header,
        "Display only — run these yourself in an elevated shell; the app will not.",
        "",
    ]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item.title} [{item.id}]")
        if item.command:
            lines.append(f"   {item.command}")
        if item.notes:
            for note_line in item.notes.splitlines():
                lines.append(f"   # {note_line}")
        lines.append("")
    return "\n".join(lines)


def refresh_checklist_on_wsl_ip_change(
    previous_wsl2_ip: str,
    new_wsl2_ip: str,
    *,
    monitoring_ip: str | None = None,
) -> list[ChecklistItem]:
    """Re-display checklist when WSL IP changes (still no auto-apply).

    Prepends portproxy delete + firewall delete so re-runs are idempotent.
    """
    items = generate_wsl2_nat_checklist(
        wsl2_ip=new_wsl2_ip,
        monitoring_ip=monitoring_ip,
        include_firewall_delete=True,
    )
    delete_old = (
        f"netsh interface portproxy delete v4tov4 "
        f"listenaddress=0.0.0.0 listenport={LOKI_PORT}"
    )
    preamble = ChecklistItem(
        id="portproxy_delete_stale",
        title=f"Remove stale portproxy (was {previous_wsl2_ip})",
        command=delete_old,
        notes=(
            f"WSL2 IP changed {previous_wsl2_ip} → {new_wsl2_ip}. "
            "Delete the old mapping before adding the new one."
        ),
    )
    # Order: delete portproxy → delete firewall → add proxy → add firewall → …
    return [preamble, *items]
