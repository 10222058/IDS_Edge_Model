"""Apply Windows Firewall allow rule for Loki :3100 (requires elevation)."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from typing import Any

from . import detect

logger = logging.getLogger(__name__)

LOKI_PORT = 3100
FIREWALL_RULE_NAME = "IoT Gateway Loki 3100"


class FirewallError(RuntimeError):
    """Operator-facing firewall failure."""


def is_elevated() -> bool | None:
    """True when running elevated on Windows. None when unknown / non-Windows."""
    if os.name != "nt" and platform.system().lower() != "windows":
        return None
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None


def _run_netsh(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["netsh", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def rule_present(*, listen_port: int = LOKI_PORT) -> bool:
    proc = _run_netsh(
        ["advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE_NAME}"]
    )
    if proc.returncode != 0:
        return False
    out = (proc.stdout or "") + (proc.stderr or "")
    return "No rules match" not in out and str(listen_port) in out


def apply_loki_firewall(
    *,
    listen_port: int = LOKI_PORT,
    force: bool = False,
) -> dict[str, Any]:
    """Idempotently ensure inbound TCP allow for Loki.

    Requires an elevated Windows process. Does not apply portproxy (native Loki).
    """
    runtime = detect.operator_runtime()
    elevated = is_elevated()
    result: dict[str, Any] = {
        "ok": False,
        "applicable": runtime == "windows",
        "runtime": runtime,
        "elevated": elevated,
        "rule_name": FIREWALL_RULE_NAME,
        "port": listen_port,
        "notes": [],
    }

    if runtime != "windows":
        result["notes"].append(
            "Firewall auto-apply is for native Windows. On Linux, allow TCP "
            f"{listen_port} on the monitoring interface if the gateway is remote."
        )
        result["ok"] = True
        result["skipped"] = True
        return result

    if elevated is False:
        raise FirewallError(
            "Administrator privileges required. Close the app and restart "
            "IoT Gateway Monitor as Administrator, then click Apply firewall again."
        )

    # Idempotent: remove then add (delete is safe when missing).
    deleted = _run_netsh(
        ["advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE_NAME}"]
    )
    result["notes"].append(
        "delete: "
        + ((deleted.stdout or deleted.stderr or "").strip() or f"rc={deleted.returncode}")
    )

    added = _run_netsh(
        [
            "advfirewall",
            "firewall",
            "add",
            "rule",
            f"name={FIREWALL_RULE_NAME}",
            "dir=in",
            "action=allow",
            "protocol=TCP",
            f"localport={listen_port}",
        ]
    )
    add_out = ((added.stdout or "") + (added.stderr or "")).strip()
    result["notes"].append(add_out or f"add rc={added.returncode}")
    if added.returncode != 0:
        raise FirewallError(
            "Failed to add Windows Firewall rule. "
            + (add_out or "Run the app as Administrator.")
        )

    present = rule_present(listen_port=listen_port)
    result["rule_present"] = present
    result["ok"] = present or force
    if present:
        result["notes"].append("Rule is present and enabled.")
    else:
        result["notes"].append(
            "Add succeeded but show-rule could not confirm — check Windows Firewall."
        )
    return result


def firewall_status(*, listen_port: int = LOKI_PORT) -> dict[str, Any]:
    runtime = detect.operator_runtime()
    elevated = is_elevated()
    present = False
    if runtime == "windows":
        try:
            present = rule_present(listen_port=listen_port)
        except Exception as exc:  # noqa: BLE001
            logger.debug("rule_present failed: %s", exc)
    return {
        "applicable": runtime == "windows",
        "runtime": runtime,
        "elevated": elevated,
        "rule_name": FIREWALL_RULE_NAME,
        "port": listen_port,
        "rule_present": present,
        "hint": (
            "Run IoT Gateway Monitor as Administrator to apply the firewall rule."
            if elevated is False
            else (
                "Firewall rule is present."
                if present
                else "Click Apply firewall to allow inbound TCP 3100."
            )
        ),
    }
