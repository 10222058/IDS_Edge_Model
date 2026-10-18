"""Selective uninstall: Gateway agent(s) and/or full PC Monitoring app-data wipe.

Rules:
- Gateway-only: remove selected / provisioned gateway agent(s); keep Loki+Prometheus.
- Monitoring: always removes every gateway registered to this monitoring panel,
  then wipes local app data (telemetry, registry, settings, keyring, Chromium).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from . import keyring_store, ssh_setup
from .paths import ensure_data_dir, load_settings, save_settings, wipe_app_data

logger = logging.getLogger(__name__)


class UninstallError(RuntimeError):
    """Operator-facing uninstall failure."""


def _gateway_provisioned(settings: dict[str, Any]) -> bool:
    host = str(settings.get("ssh_host") or settings.get("gateway_ip") or "").strip()
    return bool(host)


def list_registered_gateways() -> list[dict[str, Any]]:
    """Return registry gateway rows (id, host, …). Empty when no DB."""
    from registry.registry import Registry  # type: ignore[import-not-found]

    reg_path = ensure_data_dir() / "registry.sqlite"
    if not reg_path.is_file():
        return []
    reg = Registry(str(reg_path))
    try:
        return reg.list_gateways()
    finally:
        reg.close()


def uninstall_targets() -> dict[str, Any]:
    """Describe what Remove can act on (for Settings UI)."""
    settings = load_settings()
    rows = list_registered_gateways()
    provisioned = _gateway_provisioned(settings)
    default_gid = str(settings.get("default_gateway_id") or "gateway").strip() or "gateway"
    gateways = [
        {
            "id": str(r.get("id") or ""),
            "host": str(r.get("host") or ""),
            "ssh_user": str(r.get("ssh_user") or settings.get("ssh_user") or "ubuntu"),
        }
        for r in rows
        if str(r.get("id") or "").strip()
    ]
    # Surface the provisioned SSH host even if registry was never written.
    ids = {g["id"] for g in gateways}
    if provisioned and default_gid not in ids:
        gateways.insert(
            0,
            {
                "id": default_gid,
                "host": str(
                    settings.get("ssh_host") or settings.get("gateway_ip") or ""
                ).strip(),
                "ssh_user": str(settings.get("ssh_user") or "ubuntu"),
            },
        )
    return {
        "gateways": gateways,
        "provisioned": provisioned,
        "default_gateway_id": default_gid,
        "ssh_host": str(settings.get("ssh_host") or settings.get("gateway_ip") or ""),
        "ssh_user": str(settings.get("ssh_user") or "ubuntu"),
        "ssh_port": int(settings.get("ssh_port") or 22),
        "monitoring_hint": (
            "Removing the monitoring panel also removes every gateway "
            "registered to this panel and clears local app data on this PC. "
            "Use Gateways → Clean up to remove a single gateway."
        ),
    }


def clear_local_secrets(gateway_ids: list[str]) -> None:
    """Drop known keyring / fallback secrets for gateways + admin + signing."""
    for gid in gateway_ids:
        for kind in ("ssh", "admin", "signing"):
            try:
                keyring_store.delete_secret(gid, kind=kind)
            except Exception:  # noqa: BLE001
                logger.debug("keyring delete failed for %s/%s", gid, kind, exc_info=True)
    for account in ("local", "signing-pfx", "signing-gpg"):
        for kind in ("admin", "signing", "ssh"):
            try:
                keyring_store.delete_secret(account, kind=kind)
            except Exception:  # noqa: BLE001
                pass
    # Wipe fallback file entirely when present.
    try:
        path = keyring_store._fallback_path()
        if path.is_file():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def uninstall_gateway_over_ssh(
    *,
    host: str,
    username: str,
    port: int,
    gateway_id: str,
    sudo_password: str,
    open_ssh: Optional[Callable[..., Any]] = None,
) -> list[str]:
    """SSH to the Pi and remove the monolithic agent (leave apt mosquitto)."""
    from provisioner.agent_install import (  # type: ignore[import-not-found]
        uninstall_monolithic_agent,
    )
    from provisioner.ssh import ParamikoSSHSession  # type: ignore[import-not-found]

    key_path = ssh_setup.key_paths_for(gateway_id).private
    if not key_path.is_file():
        raise UninstallError(
            f"SSH private key missing for gateway '{gateway_id}' — pin host key "
            "and install ed25519 before uninstalling the agent, or remove the "
            "registry row only."
        )
    session_factory = open_ssh or ParamikoSSHSession
    ssh = session_factory(
        host,
        username,
        key_filename=str(key_path),
        port=port,
    )
    try:
        return uninstall_monolithic_agent(ssh, sudo_password=sudo_password)
    finally:
        try:
            ssh.close()
        except Exception:  # noqa: BLE001
            pass


def remove_gateway_from_registry(gateway_id: str) -> bool:
    from registry.registry import Registry  # type: ignore[import-not-found]

    reg_path = ensure_data_dir() / "registry.sqlite"
    if not reg_path.is_file():
        return False
    reg = Registry(str(reg_path))
    try:
        return reg.delete_gateway(gateway_id)
    finally:
        reg.close()


def remove_all_gateways_from_registry() -> list[str]:
    """Delete every gateway (and cascaded devices) from the local registry."""
    removed: list[str] = []
    for row in list_registered_gateways():
        gid = str(row.get("id") or "").strip()
        if not gid:
            continue
        if remove_gateway_from_registry(gid):
            removed.append(gid)
    return removed


def _gateway_jobs(
    *,
    settings: dict[str, Any],
    gateway_id: str | None,
    all_gateways: bool,
) -> list[dict[str, Any]]:
    """Build the list of gateways to SSH-remove / drop from registry."""
    targets = uninstall_targets()
    rows = targets["gateways"]
    if all_gateways or not gateway_id:
        jobs = list(rows)
    else:
        want = gateway_id.strip()
        jobs = [g for g in rows if g["id"] == want]
        if not jobs and _gateway_provisioned(settings):
            jobs = [
                {
                    "id": want
                    or str(settings.get("default_gateway_id") or "gateway"),
                    "host": str(
                        settings.get("ssh_host") or settings.get("gateway_ip") or ""
                    ).strip(),
                }
            ]
    # Ensure SSH settings host is preferred for the default gateway.
    default_host = str(
        settings.get("ssh_host") or settings.get("gateway_ip") or ""
    ).strip()
    default_gid = str(settings.get("default_gateway_id") or "gateway").strip() or "gateway"
    out: list[dict[str, Any]] = []
    for g in jobs:
        host = str(g.get("host") or "").strip()
        gid = str(g.get("id") or "").strip() or "gateway"
        if gid == default_gid and default_host:
            host = default_host
        username = str(
            g.get("ssh_user") or settings.get("ssh_user") or "ubuntu"
        ).strip()
        out.append(
            {
                "id": gid,
                "host": host,
                "username": username,
                "port": int(settings.get("ssh_port") or 22),
            }
        )
    return out


def run_uninstall(
    *,
    gateway: bool,
    monitoring: bool,
    sudo_password: str = "",
    gateway_id: str | None = None,
    stop_telemetry: Callable[[], None],
    stop_services: Callable[[], None],
    telemetry_uninstall: Callable[[], dict[str, Any]],
    reset_telemetry_mgr: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Execute selective uninstall.

    Monitoring always implies every registered gateway is removed (SSH when
    possible, then registry + full app-data wipe). Gateway-only leaves
    Loki/Prometheus running.
    """
    if not gateway and not monitoring:
        raise UninstallError("Select Gateway and/or Monitoring to uninstall")

    # Monitoring ⇒ all gateways registered to this panel.
    if monitoring:
        gateway = True

    settings = load_settings()
    all_gateways = bool(monitoring)
    jobs = _gateway_jobs(
        settings=settings,
        gateway_id=gateway_id,
        all_gateways=all_gateways,
    )

    result: dict[str, Any] = {
        "ok": True,
        "gateway": None,
        "gateways": [],
        "monitoring": None,
        "app_data": None,
        "notes": [],
    }

    # ── Gateway agent(s) ──────────────────────────────────────────────
    if gateway:
        if not jobs:
            result["gateway"] = {
                "ok": True,
                "skipped": True,
                "detail": "No registered or provisioned gateway to remove.",
            }
            result["notes"].append("Gateway: nothing registered locally")
        else:
            need_sudo = any(j.get("host") for j in jobs)
            if need_sudo and not (sudo_password or "").strip():
                raise UninstallError(
                    "Gateway sudo password required to remove the agent on the Pi"
                )
            gateway_results: list[dict[str, Any]] = []
            secret_ids: list[str] = []
            for job in jobs:
                gid = job["id"]
                host = job["host"]
                entry: dict[str, Any] = {"id": gid, "host": host, "ok": True}
                if host:
                    try:
                        notes = uninstall_gateway_over_ssh(
                            host=host,
                            username=job["username"],
                            port=job["port"],
                            gateway_id=gid,
                            sudo_password=sudo_password,
                        )
                        entry["notes"] = notes
                        result["notes"].extend(
                            [f"[{gid}] {n}" for n in notes]
                        )
                    except UninstallError as exc:
                        # On monitoring wipe we still clear local state; surface error.
                        entry["ok"] = False
                        entry["error"] = str(exc)
                        result["notes"].append(f"[{gid}] {exc}")
                        if not monitoring:
                            raise
                        result["ok"] = False
                    except Exception as exc:  # noqa: BLE001
                        entry["ok"] = False
                        entry["error"] = str(exc)
                        result["notes"].append(f"[{gid}] {exc}")
                        if not monitoring:
                            raise UninstallError(str(exc)) from exc
                        result["ok"] = False
                else:
                    entry["skipped"] = True
                    entry["detail"] = "No host — registry row only"
                    result["notes"].append(f"[{gid}] registry row only (no SSH host)")

                removed = remove_gateway_from_registry(gid)
                entry["registry_removed"] = removed
                secret_ids.append(gid)
                gateway_results.append(entry)

            clear_local_secrets(secret_ids)

            # Drop SSH-related settings when we touched the default provisioned gw,
            # or when Monitoring is wiping everything anyway.
            if monitoring or any(
                g["id"]
                == (
                    str(settings.get("default_gateway_id") or "gateway").strip()
                    or "gateway"
                )
                for g in jobs
            ):
                for key in (
                    "ssh_host",
                    "ssh_user",
                    "ssh_port",
                    "provision_backend",
                    "gateway_fingerprint",
                ):
                    settings.pop(key, None)
                save_settings(settings)

            result["gateways"] = gateway_results
            result["gateway"] = {
                "ok": all(g.get("ok", True) for g in gateway_results),
                "count": len(gateway_results),
                "ids": [g["id"] for g in gateway_results],
            }

    # ── Monitoring (+ full app-data wipe) ─────────────────────────────
    if monitoring:
        # Ensure every remaining registry gateway is gone before wipe.
        leftover = remove_all_gateways_from_registry()
        if leftover:
            result["notes"].append(
                "Cleared remaining registry gateways: " + ", ".join(leftover)
            )
        try:
            stop_services()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stop_services during uninstall: %s", exc)
        try:
            stop_telemetry()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stop_telemetry during uninstall: %s", exc)
        tel = telemetry_uninstall()
        if reset_telemetry_mgr is not None:
            reset_telemetry_mgr()
        # Clear secrets for any remaining known ids before wiping files.
        ids = [g["id"] for g in (result.get("gateways") or [])]
        clear_local_secrets([*ids, "gateway", "local"])
        wiped = wipe_app_data()
        result["monitoring"] = tel
        result["app_data"] = wiped
        result["notes"].append(
            f"Telemetry removed under {tel.get('root')}; "
            f"app data wipe removed={wiped.get('removed')} skipped={wiped.get('skipped')}"
        )
        if tel.get("errors") or wiped.get("errors"):
            result["ok"] = False

    return result
