"""HTTP Setup Wizard UI + JSON API (stdlib).

Serves ``ui/`` and exposes wizard steps that shell out to detect / nic /
checklist / ssh_setup / launcher. Native Windows applies Loki :3100 firewall when elevated.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import (
    admin_auth,
    detect,
    discover,
    firewall_apply,
    keyring_store,
    ssh_setup,
    uninstall as uninstall_mod,
)
from .launcher import ProcessManager, open_in_browser, wait_http
from .paths import (
    CONTROL_HOST,
    CONTROL_PORT,
    STREAMLIT_PORT,
    WIZARD_PORT,
    control_service_dir,
    desktop_ui_dir,
    ensure_data_dir,
    load_settings,
    save_settings,
)

logger = logging.getLogger(__name__)

# Ensure control-service packages importable for nic/checklist/actions/signing.
_CS = str(control_service_dir())
if _CS not in sys.path:
    sys.path.insert(0, _CS)


class WizardState:
    def __init__(self) -> None:
        self.manager = ProcessManager()
        self.firewall_confirmed = False
        self.lock = threading.Lock()
        self.admin = admin_auth.AdminGate()
        self.last_signing_export: str | None = None
        self._telemetry = None
        # Background Install monitoring job (avoids long-blocking HTTP fetches).
        self._telemetry_job: dict[str, Any] = {
            "state": "idle",  # idle | running | ok | error
            "detail": "",
            "error": None,
        }
        self._telemetry_thread: threading.Thread | None = None

    @property
    def telemetry(self):
        """Lazy native Loki/Prometheus manager (no Docker on the operator PC)."""
        if self._telemetry is None:
            from telemetry.native import (  # type: ignore[import-not-found]
                NativeTelemetryManager,
            )

            from .paths import ensure_data_dir

            self._telemetry = NativeTelemetryManager(ensure_data_dir() / "telemetry")
        return self._telemetry

    def telemetry_job_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self._telemetry_job)

    def start_telemetry_job(
        self, *, gateway_ip: str | None = None, force: bool = False
    ) -> dict[str, Any]:
        """Kick off download+start on a daemon thread; poll via telemetry_job_snapshot."""
        with self.lock:
            if self._telemetry_job.get("state") == "running":
                return {"started": False, "job": dict(self._telemetry_job)}
            self._telemetry_job = {
                "state": "running",
                "detail": "Installing monitoring…",
                "error": None,
            }

        def _run() -> None:
            try:
                tel = self.telemetry
                # Fast path: already healthy → mark ok without re-downloading.
                snap0 = tel.snapshot()
                if snap0.ready and not force:
                    with self.lock:
                        self._telemetry_job = {
                            "state": "ok",
                            "detail": snap0.detail
                            or "Loki and Prometheus are running.",
                            "error": None,
                        }
                    return
                settings = load_settings()
                gw = gateway_ip or str(settings.get("gateway_ip") or "127.0.0.1")
                # start() downloads binaries only when ports are free / not ready.
                st = tel.start(
                    gateway_ip=gw, wait=True, ensure_bins=True
                )
                if force and not st.running:
                    # Force path: re-fetch then retry once.
                    tel.ensure_binaries(force=True)
                    st = tel.start(
                        gateway_ip=gw, wait=True, ensure_bins=False
                    )
                snap = tel.snapshot()
                ready = bool(st.running) or bool(snap.ready)
                detail = st.detail or snap.detail or ""
                with self.lock:
                    self._telemetry_job = {
                        "state": "ok" if ready else "error",
                        "detail": detail
                        if ready
                        else (detail or "Install failed."),
                        "error": None if ready else (detail or "Install failed."),
                    }
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self._telemetry_job = {
                        "state": "error",
                        "detail": str(e),
                        "error": str(e),
                    }

        thread = threading.Thread(
            target=_run, name="iotgw-telemetry-ensure", daemon=True
        )
        self._telemetry_thread = thread
        thread.start()
        return {"started": True, "job": self.telemetry_job_snapshot()}


STATE = WizardState()
_LAB_MANAGER = None


def _lab_manager():
    """Lazy LabFakeSensorManager under app data (optional demos)."""
    global _LAB_MANAGER
    if _LAB_MANAGER is None:
        from lab.lifecycle import LabFakeSensorManager  # type: ignore[import-not-found]

        from .paths import ensure_data_dir

        _LAB_MANAGER = LabFakeSensorManager(ensure_data_dir() / "lab")
    return _LAB_MANAGER


def _lab_status_dict(st: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(st)


def _admin_token(handler: BaseHTTPRequestHandler) -> str | None:
    return handler.headers.get("X-Admin-Session") or None


def _normalize_platform(value: object) -> str:
    plat = str(value or "auto").strip().lower()
    return plat if plat in ("windows", "linux", "auto") else "auto"


def _require_abs_path(raw: str, *, label: str) -> str:
    """Admin signing paths: absolute, no NUL. Unlocked admin ≡ local FS access."""
    text = (raw or "").strip()
    if not text or "\x00" in text:
        raise ValueError(f"{label}: empty or invalid path")
    from pathlib import Path

    p = Path(text)
    if not p.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return str(p)


def _json_response(handler: BaseHTTPRequestHandler, code: int, body: Any) -> None:
    raw = json.dumps(body).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _handle_api(
    handler: BaseHTTPRequestHandler,
    method: str,
    path: str,
    *,
    query: str = "",
) -> None:
    try:
        qs = parse_qs(query)

        if method == "GET" and path == "/api/wizard/env":
            snap = detect.environment_snapshot()
            settings = load_settings()
            try:
                tel = STATE.telemetry.snapshot()
                job = STATE.telemetry_job_snapshot()
                snap["telemetry"] = {
                    "backend": "native",
                    "ready": tel.ready,
                    "binaries_present": tel.binaries_present,
                    "loki_running": tel.loki_running,
                    "prometheus_running": tel.prometheus_running,
                    "detail": tel.detail,
                    "progress": tel.progress,
                    "job": job,
                    "hint": (
                        "Loki and Prometheus are running."
                        if tel.ready
                        else "Install monitoring to download and start Loki and Prometheus."
                    ),
                }
            except Exception as e:  # noqa: BLE001
                snap["telemetry"] = {
                    "backend": "native",
                    "ready": False,
                    "detail": str(e),
                    "hint": "Monitoring is unavailable.",
                    "job": STATE.telemetry_job_snapshot(),
                }
            # Packaged path: native Loki/Prometheus. Docker and WSL are optional.
            snap["docker_required"] = False
            snap["wsl_required"] = False
            # First-run vs returning operator: wizard only until setup finishes.
            setup_complete = bool(settings.get("setup_complete"))
            tel_ready = bool((snap.get("telemetry") or {}).get("ready"))
            snap["setup_complete"] = setup_complete or tel_ready
            snap["entry"] = "app" if snap["setup_complete"] else "wizard"
            _json_response(handler, 200, snap)
            return

        if method == "POST" and path == "/api/wizard/setup/complete":
            settings = load_settings()
            settings["setup_complete"] = True
            save_settings(settings)
            _json_response(
                handler,
                200,
                {"ok": True, "setup_complete": True, "entry": "app"},
            )
            return

        if method == "POST" and path == "/api/wizard/telemetry/ensure":
            body = _read_json(handler)
            gateway_ip = str(body.get("gateway_ip") or "").strip() or None
            force = bool(body.get("force", False))
            # background=True (Wizard default): return immediately and poll status.
            # background=False: block until ready (tests / automation).
            background = bool(body.get("background", False))
            if background:
                out = STATE.start_telemetry_job(gateway_ip=gateway_ip, force=force)
                tel = STATE.telemetry.snapshot()
                job = out["job"]
                _json_response(
                    handler,
                    200,
                    {
                        "ok": True,
                        "background": True,
                        "started": out["started"],
                        "job": job,
                        "telemetry": {
                            "backend": "native",
                            "ready": tel.ready,
                            "binaries_present": tel.binaries_present,
                            "loki_running": tel.loki_running,
                            "prometheus_running": tel.prometheus_running,
                            "detail": job.get("detail") or tel.detail,
                            "progress": tel.progress,
                        },
                    },
                )
                return
            try:
                settings = load_settings()
                gw = gateway_ip or str(settings.get("gateway_ip") or "127.0.0.1")
                # start() probes first, then downloads only if needed.
                st = STATE.telemetry.start(
                    gateway_ip=gw, wait=True, ensure_bins=True
                )
                if force:
                    STATE.telemetry.stop()
                    STATE.telemetry.ensure_binaries(force=True)
                    st = STATE.telemetry.start(
                        gateway_ip=gw, wait=True, ensure_bins=False
                    )
                # Prefer start() result — snapshot used to overwrite Ready with
                # binary-fetch progress ("already present") while child handles
                # were unset after a previous session.
                tel = STATE.telemetry.snapshot()
                ready = bool(st.running) or bool(tel.ready)
                detail = st.detail or tel.detail or ""
                with STATE.lock:
                    STATE._telemetry_job = {
                        "state": "ok" if ready else "error",
                        "detail": detail,
                        "error": None if ready else detail,
                    }
                _json_response(
                    handler,
                    200,
                    {
                        "ok": ready,
                        "background": False,
                        "telemetry": {
                            "backend": "native",
                            "ready": ready,
                            "binaries_present": tel.binaries_present,
                            "loki_running": tel.loki_running
                            or bool(getattr(st.loki, "ok", False)),
                            "prometheus_running": tel.prometheus_running
                            or bool(getattr(st.prometheus, "ok", False)),
                            "detail": detail,
                            "progress": tel.progress,
                        },
                        "job": STATE.telemetry_job_snapshot(),
                    },
                )
            except Exception as e:  # noqa: BLE001
                with STATE.lock:
                    STATE._telemetry_job = {
                        "state": "error",
                        "detail": str(e),
                        "error": str(e),
                    }
                _json_response(handler, 500, {"error": str(e), "ok": False})
            return

        if method == "POST" and path == "/api/wizard/telemetry/stop":
            STATE.telemetry.stop()
            with STATE.lock:
                if STATE._telemetry_job.get("state") != "running":
                    STATE._telemetry_job = {
                        "state": "idle",
                        "detail": "Stopped.",
                        "error": None,
                    }
            _json_response(handler, 200, {"ok": True})
            return

        if method == "GET" and path == "/api/wizard/uninstall/targets":
            try:
                _json_response(handler, 200, uninstall_mod.uninstall_targets())
            except Exception as e:  # noqa: BLE001
                logger.exception("uninstall targets failed")
                _json_response(handler, 500, {"ok": False, "error": str(e)})
            return

        if method == "POST" and path == "/api/wizard/uninstall":
            body = _read_json(handler)
            do_gateway = bool(body.get("gateway", False))
            do_monitoring = bool(body.get("monitoring", False))
            # Coupling: Monitoring always removes every registered gateway.
            if do_monitoring:
                do_gateway = True
            sudo_password = str(body.get("sudo_password") or "")
            gateway_id = str(body.get("gateway_id") or "").strip() or None
            try:
                out = uninstall_mod.run_uninstall(
                    gateway=do_gateway,
                    monitoring=do_monitoring,
                    sudo_password=sudo_password,
                    gateway_id=None if do_monitoring else gateway_id,
                    stop_telemetry=lambda: STATE.telemetry.stop(),
                    stop_services=lambda: STATE.manager.stop_all(),
                    telemetry_uninstall=lambda: STATE.telemetry.uninstall(),
                    reset_telemetry_mgr=lambda: setattr(STATE, "_telemetry", None),
                )
                with STATE.lock:
                    STATE._telemetry_job = {
                        "state": "idle",
                        "detail": "Uninstalled." if out.get("ok") else "Uninstall finished with errors.",
                        "error": None if out.get("ok") else "uninstall errors",
                    }
                    STATE.firewall_confirmed = False
                # Never echo sudo password back.
                sudo_password = ""
                _json_response(handler, 200 if out.get("ok") else 207, out)
            except uninstall_mod.UninstallError as e:
                _json_response(handler, 409, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                logger.exception("uninstall failed")
                _json_response(handler, 500, {"ok": False, "error": str(e)})
            return

        if method == "GET" and path == "/api/wizard/telemetry/status":
            job = STATE.telemetry_job_snapshot()
            # While installing, avoid slow HTTP probes so download progress stays fresh.
            if job.get("state") == "running":
                tel_mgr = STATE.telemetry
                progress = str(getattr(tel_mgr, "last_progress", "") or "")
                bins = False
                bin_fn = getattr(tel_mgr, "_bin", None)
                if callable(bin_fn):
                    try:
                        bins = bool(
                            bin_fn("loki").is_file() and bin_fn("prometheus").is_file()
                        )
                    except Exception:  # noqa: BLE001
                        bins = False
                _json_response(
                    handler,
                    200,
                    {
                        "backend": "native",
                        "ready": False,
                        "binaries_present": bins,
                        "loki_running": False,
                        "prometheus_running": False,
                        "detail": progress or job.get("detail") or "Installing…",
                        "progress": progress,
                        "job": job,
                    },
                )
                return
            tel = STATE.telemetry.snapshot()
            detail = tel.detail
            if job.get("state") == "error":
                detail = job.get("detail") or detail
            _json_response(
                handler,
                200,
                {
                    "backend": "native",
                    "ready": tel.ready,
                    "binaries_present": tel.binaries_present,
                    "loki_running": tel.loki_running,
                    "prometheus_running": tel.prometheus_running,
                    "detail": detail,
                    "progress": tel.progress,
                    "job": job,
                },
            )
            return

        if method == "GET" and path == "/api/wizard/control":
            # Actions chrome uses this to target FastAPI (CORS-enabled).
            import os

            settings = load_settings()
            url = (
                str(settings.get("control_url") or "").strip()
                or f"http://{CONTROL_HOST}:{CONTROL_PORT}"
            )
            token = (
                str(settings.get("api_token") or "").strip()
                or os.environ.get("IOTGW_API_TOKEN", "").strip()
                or None
            )
            _json_response(
                handler,
                200,
                {
                    "url": url,
                    "api_token": token,
                },
            )
            return

        if method == "GET" and path == "/api/wizard/nics":
            from telemetry.nic import (  # type: ignore[import-not-found]
                list_ipv4_candidates,
                pick_default_monitoring_ip,
            )

            settings = load_settings()
            mode_q = (qs.get("windows_net_mode") or [None])[0]
            mode = mode_q if mode_q not in (None, "") else settings.get("windows_net_mode")
            if mode_q == "":
                mode = None
            cands = list_ipv4_candidates(windows_net_mode=mode)
            default = pick_default_monitoring_ip(
                cands, gateway_ip=settings.get("gateway_ip")
            )
            _json_response(
                handler,
                200,
                {
                    "candidates": [
                        {"name": c.name, "ip": c.ip, "prefix": c.prefix} for c in cands
                    ],
                    "default": (
                        {"name": default.name, "ip": default.ip, "prefix": default.prefix}
                        if default
                        else None
                    ),
                    "windows_net_mode": mode,
                },
            )
            return

        if method == "POST" and path == "/api/wizard/nics/select":
            body = _read_json(handler)
            ip = str(body.get("monitoring_ip") or "").strip()
            nic = str(body.get("nic_name") or "").strip()
            gateway_ip = str(body.get("gateway_ip") or "").strip()
            if not ip and not gateway_ip:
                _json_response(
                    handler, 400, {"error": "monitoring_ip or gateway_ip required"}
                )
                return
            settings = load_settings()
            if ip:
                settings["monitoring_ip"] = ip
            if nic:
                settings["nic_name"] = nic
            if body.get("windows_net_mode"):
                settings["windows_net_mode"] = body["windows_net_mode"]
            if gateway_ip:
                settings["gateway_ip"] = gateway_ip
            save_settings(settings)
            _json_response(handler, 200, {"ok": True, "settings": settings})
            return

        if method == "POST" and path == "/api/wizard/settings/gateway":
            body = _read_json(handler)
            settings = load_settings()
            gid = str(body.get("gateway_id") or "").strip()
            host = str(body.get("ssh_host") or body.get("gateway_ip") or "").strip()
            if not host:
                _json_response(handler, 400, {"error": "ssh_host or gateway_ip required"})
                return
            if gid:
                settings["default_gateway_id"] = gid
            settings["gateway_ip"] = str(body.get("gateway_ip") or host).strip()
            settings["ssh_host"] = host
            if body.get("ssh_user") is not None:
                settings["ssh_user"] = str(body.get("ssh_user") or "ubuntu").strip()
            if body.get("ssh_port") is not None:
                try:
                    settings["ssh_port"] = int(body.get("ssh_port") or 22)
                except (TypeError, ValueError):
                    _json_response(handler, 400, {"error": "invalid ssh_port"})
                    return
            save_settings(settings)
            _json_response(handler, 200, {"ok": True, "settings": settings})
            return

        if method == "POST" and path == "/api/wizard/discover/gateways":
            body = _read_json(handler)
            try:
                port = int(body.get("port") or discover.DEFAULT_SSH_PORT)
            except (TypeError, ValueError):
                _json_response(handler, 400, {"error": "invalid port"})
                return
            try:
                timeout_s = float(body.get("timeout_s") or discover.DEFAULT_TIMEOUT_S)
            except (TypeError, ValueError):
                timeout_s = discover.DEFAULT_TIMEOUT_S
            monitoring_ip = str(body.get("monitoring_ip") or "").strip() or None
            prefix = body.get("prefix")
            try:
                pref = int(prefix) if prefix is not None and prefix != "" else None
            except (TypeError, ValueError):
                _json_response(handler, 400, {"error": "invalid prefix"})
                return
            try:
                out = discover.discover_gateways(
                    monitoring_ip=monitoring_ip,
                    prefix=pref,
                    port=port,
                    timeout_s=timeout_s,
                )
                _json_response(handler, 200, out)
            except ValueError as e:
                _json_response(handler, 400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                logger.exception("gateway discover failed")
                _json_response(handler, 500, {"error": str(e)})
            return

        if method == "GET" and path == "/api/wizard/firewall/status":
            try:
                st = firewall_apply.firewall_status()
                st["confirmed"] = STATE.firewall_confirmed or bool(
                    st.get("rule_present")
                )
                _json_response(handler, 200, st)
            except Exception as e:  # noqa: BLE001
                logger.exception("firewall status failed")
                _json_response(handler, 500, {"error": str(e)})
            return

        if method == "POST" and path == "/api/wizard/firewall/apply":
            try:
                out = firewall_apply.apply_loki_firewall()
                if out.get("ok"):
                    STATE.firewall_confirmed = True
                    settings = load_settings()
                    settings["firewall_checklist_confirmed"] = True
                    save_settings(settings)
                _json_response(handler, 200, out)
            except firewall_apply.FirewallError as e:
                _json_response(handler, 403, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                logger.exception("firewall apply failed")
                _json_response(handler, 500, {"ok": False, "error": str(e)})
            return

        if method == "GET" and path == "/api/wizard/checklist":
            from telemetry.checklist import (  # type: ignore[import-not-found]
                format_checklist_for_display,
                generate_windows_native_checklist,
                generate_wsl2_nat_checklist,
                refresh_checklist_on_wsl_ip_change,
            )

            settings = load_settings()
            wsl = detect.detect_wsl()
            runtime = detect.operator_runtime()
            monitoring_ip = str(settings.get("monitoring_ip") or "").strip() or None
            fw = firewall_apply.firewall_status()

            # Packaged Windows .exe: native Loki on 0.0.0.0:3100 — firewall only.
            # Prefer in-app apply when elevated; keep commands as fallback detail.
            if runtime == "windows":
                items = generate_windows_native_checklist(monitoring_ip=monitoring_ip)
                payload_items = [
                    {
                        "id": i.id,
                        "title": i.title,
                        "command": i.command,
                        "notes": i.notes,
                    }
                    for i in items
                ]
                confirmed = STATE.firewall_confirmed or bool(fw.get("rule_present"))
                _json_response(
                    handler,
                    200,
                    {
                        "applicable": True,
                        "path": "windows_native",
                        "monitoring_ip": monitoring_ip,
                        "items": payload_items,
                        "display": format_checklist_for_display(items),
                        "auto_apply": True,
                        "elevated": fw.get("elevated"),
                        "rule_present": fw.get("rule_present"),
                        "note": (
                            "The app applies the Windows Firewall rule when run as "
                            "Administrator (inbound TCP 3100). WSL is not required."
                        ),
                        "localhost_forwarding_note": wsl.localhost_forwarding_note,
                        "confirmed": confirmed,
                    },
                )
                return

            # Legacy: app running inside WSL2 still uses portproxy guidance.
            wsl_ip = str(settings.get("wsl2_ip") or wsl.wsl_ip or "").strip()
            if runtime != "wsl" or not wsl_ip:
                _json_response(
                    handler,
                    200,
                    {
                        "applicable": False,
                        "path": runtime,
                        "reason": (
                            "Firewall checklist is for Windows. On Linux, allow "
                            "TCP 3100 on MONITORING_IP if the gateway is remote."
                        ),
                        "items": [],
                        "display": "",
                        "localhost_forwarding_note": wsl.localhost_forwarding_note,
                    },
                )
                return
            prev = str(settings.get("wsl2_ip_prev") or "")
            if prev and prev != wsl_ip:
                items = refresh_checklist_on_wsl_ip_change(
                    prev, wsl_ip, monitoring_ip=monitoring_ip
                )
            else:
                items = generate_wsl2_nat_checklist(
                    wsl2_ip=wsl_ip, monitoring_ip=monitoring_ip
                )
            settings["wsl2_ip"] = wsl_ip
            save_settings(settings)
            payload_items = [
                {
                    "id": i.id,
                    "title": i.title,
                    "command": i.command,
                    "notes": i.notes,
                }
                for i in items
            ]
            _json_response(
                handler,
                200,
                {
                    "applicable": True,
                    "path": "wsl2_nat",
                    "wsl2_ip": wsl_ip,
                    "monitoring_ip": monitoring_ip,
                    "items": payload_items,
                    "display": format_checklist_for_display(items),
                    "auto_apply": False,
                    "note": (
                        "Display only — run elevated yourself; the app will not "
                        "apply firewall/portproxy rules (K18)."
                    ),
                    "localhost_forwarding_note": wsl.localhost_forwarding_note,
                    "confirmed": STATE.firewall_confirmed,
                },
            )
            return

        if method == "POST" and path == "/api/wizard/checklist/confirm":
            STATE.firewall_confirmed = True
            settings = load_settings()
            settings["firewall_checklist_confirmed"] = True
            # Legacy WSL path only: remember WSL IP so a later change re-shows.
            if detect.operator_runtime() == "wsl":
                wsl = detect.detect_wsl()
                if wsl.wsl_ip:
                    settings["wsl2_ip_prev"] = settings.get("wsl2_ip") or wsl.wsl_ip
                    settings["wsl2_ip"] = wsl.wsl_ip
            save_settings(settings)
            _json_response(handler, 200, {"ok": True, "confirmed": True})
            return

        if method == "POST" and path == "/api/wizard/ssh/fetch-host-key":
            body = _read_json(handler)
            host = str(body.get("host") or "").strip()
            port = int(body.get("port") or 22)
            if not host:
                _json_response(handler, 400, {"error": "host required"})
                return
            info = ssh_setup.fetch_host_key(host, port=port)
            _json_response(handler, 200, ssh_setup.host_key_info_dict(info))
            return

        if method == "POST" and path == "/api/wizard/ssh/pin-host-key":
            body = _read_json(handler)
            required = ("host", "port", "key_type", "fingerprint_sha256", "base64")
            if any(k not in body for k in required):
                _json_response(handler, 400, {"error": f"need {required}"})
                return
            info = ssh_setup.HostKeyInfo(
                host=str(body["host"]),
                port=int(body["port"]),
                key_type=str(body["key_type"]),
                fingerprint_sha256=str(body["fingerprint_sha256"]),
                base64=str(body["base64"]),
            )
            ssh_setup.pin_host_key(info)
            _json_response(handler, 200, {"ok": True, "pinned": info.fingerprint_sha256})
            return

        if method == "POST" and path == "/api/wizard/ssh/install-key":
            body = _read_json(handler)
            host = str(body.get("host") or "").strip()
            username = str(body.get("username") or "").strip()
            password = str(body.get("password") or "")
            gateway_id = str(body.get("gateway_id") or host or "default").strip()
            port = int(body.get("port") or 22)
            host_key_b64 = body.get("host_key_base64")
            if not host or not username or not password:
                _json_response(
                    handler, 400, {"error": "host, username, password required"}
                )
                return
            # Pin is mandatory — reject TOFU / WarningPolicy password installs.
            try:
                ssh_setup.resolve_required_pin(
                    host,
                    port,
                    str(host_key_b64) if host_key_b64 else None,
                )
            except ValueError as e:
                _json_response(handler, 400, {"error": str(e)})
                return
            result = ssh_setup.install_pubkey(
                host,
                username,
                password,
                gateway_id,
                port=port,
                host_key_base64=str(host_key_b64) if host_key_b64 else None,
            )
            settings = load_settings()
            settings["default_gateway_id"] = gateway_id
            save_settings(settings)
            _json_response(handler, 200, result)
            return

        if method == "POST" and path == "/api/wizard/services/start":
            body = _read_json(handler)
            # Omit streamlit → False so API clients match the native-Monitoring default.
            start_st = bool(body.get("streamlit", False))
            with STATE.lock:
                ctrl = STATE.manager.start_control_service()
                ok_ctrl = wait_http(ctrl.url, timeout=45, path="/health")
                st_ok = None
                st_url = None
                st_error = None
                if start_st:
                    try:
                        st = STATE.manager.start_streamlit(control_url=ctrl.url)
                        st_url = st.url
                        st_ok = wait_http(st.url, timeout=90)
                    except FileNotFoundError as e:
                        st_error = str(e)
                        st_ok = False
            _json_response(
                handler,
                200,
                {
                    "control": {
                        "url": ctrl.url,
                        "healthy": ok_ctrl,
                        "status": STATE.manager.status()["control"],
                    },
                    "streamlit": {
                        "url": st_url,
                        "healthy": st_ok,
                        "error": st_error,
                        "status": STATE.manager.status()["streamlit"],
                    },
                },
            )
            return

        if method == "GET" and path == "/api/wizard/services/status":
            _json_response(handler, 200, STATE.manager.status())
            return

        if method == "POST" and path == "/api/wizard/services/stop":
            STATE.manager.stop_all()
            _json_response(handler, 200, {"ok": True})
            return

        if method == "POST" and path == "/api/wizard/open-streamlit":
            body = _read_json(handler)
            st_status = STATE.manager.status().get("streamlit") or {}
            if not st_status.get("running"):
                _json_response(
                    handler,
                    400,
                    {
                        "error": "Streamlit is not running — start services with streamlit:true first",
                        "status": st_status,
                    },
                )
                return
            url = str(body.get("url") or f"http://{CONTROL_HOST}:{STREAMLIT_PORT}")
            open_in_browser(url)
            _json_response(handler, 200, {"ok": True, "url": url})
            return

        if method == "POST" and path == "/api/wizard/provision":
            # Docker-free monolithic agent install over SSH (uses pinned key from step 3).
            body = _read_json(handler)
            settings = load_settings()
            host = str(body.get("host") or settings.get("ssh_host") or "").strip()
            username = str(
                body.get("username") or settings.get("ssh_user") or "ubuntu"
            ).strip()
            port = int(body.get("port") or settings.get("ssh_port") or 22)
            gateway_id = str(
                body.get("gateway_id") or settings.get("default_gateway_id") or "gateway"
            ).strip()
            monitoring_ip = str(
                body.get("monitoring_ip") or settings.get("monitoring_ip") or ""
            ).strip()
            gateway_ip = str(
                body.get("gateway_ip") or settings.get("gateway_ip") or host
            ).strip()
            # Never persist sudo password to settings / disk / logs.
            sudo_password = str(body.get("sudo_password") or "")
            if not host:
                _json_response(handler, 400, {"error": "SSH host required (complete step 3)"})
                return
            if not monitoring_ip:
                _json_response(
                    handler,
                    400,
                    {"error": "MONITORING_IP required (complete step 2)"},
                )
                return
            if not sudo_password.strip():
                _json_response(
                    handler,
                    400,
                    {
                        "error": "Gateway sudo password required — used for apt/systemd install only",
                    },
                )
                return
            key_path = ssh_setup.key_paths_for(gateway_id).private
            if not key_path.is_file():
                _json_response(
                    handler,
                    400,
                    {
                        "error": "SSH key not installed yet — pin host key and install ed25519 first",
                    },
                )
                return
            try:
                from provisioner.provision import (  # type: ignore[import-not-found]
                    ProvisionConfig,
                    provision,
                )
                from provisioner.ssh import ParamikoSSHSession  # type: ignore[import-not-found]

                ssh = ParamikoSSHSession(
                    host,
                    username,
                    key_filename=str(key_path),
                    port=port,
                )
                try:
                    result = provision(
                        ssh,
                        ProvisionConfig(
                            gateway_ip=gateway_ip or host,
                            monitoring_ip=monitoring_ip,
                            backend="agent",
                            sudo_password=sudo_password,
                        ),
                    )
                finally:
                    ssh.close()
                    sudo_password = ""  # drop local reference ASAP
                settings["gateway_ip"] = gateway_ip or host
                settings["ssh_host"] = host
                settings["ssh_user"] = username
                settings["ssh_port"] = port
                settings["provision_backend"] = "agent"
                save_settings(settings)
                _json_response(
                    handler,
                    200,
                    {
                        "ok": True,
                        "backend": result.backend,
                        "install_root": result.install_root,
                        "agent_installed": result.agent_installed,
                        "notes": result.notes,
                        "probes": result.probes,
                    },
                )
            except Exception as e:  # noqa: BLE001
                _json_response(handler, 500, {"error": str(e), "ok": False})
            return

        # --- Lab (optional fake_sensor; not production) — UI hidden this build ---
        if method == "GET" and path == "/api/lab/defaults":
            settings = load_settings()
            from lab.lifecycle import (  # type: ignore[import-not-found]
                DEFAULT_DURATION_MINUTES,
                STORAGE_OVERFLOW_WARNING,
            )

            _json_response(
                handler,
                200,
                {
                    "gateway_ip": settings.get("gateway_ip") or "",
                    "duration_minutes": DEFAULT_DURATION_MINUTES,
                    "warning": STORAGE_OVERFLOW_WARNING,
                },
            )
            return

        if method == "GET" and path == "/api/lab/fake-sensors/status":
            _json_response(handler, 200, _lab_status_dict(_lab_manager().status()))
            return

        if method == "POST" and path == "/api/lab/fake-sensors/start":
            body = _read_json(handler)
            gw = str(body.get("gateway_ip") or "").strip()
            if not gw:
                _json_response(handler, 400, {"error": "gateway_ip required"})
                return
            sensors = body.get("sensors")
            if isinstance(sensors, str):
                sensors = [s.strip() for s in sensors.split(",") if s.strip()]
            raw_dur = body.get("duration_minutes", 10)
            if raw_dur is None or raw_dur == "":
                duration_minutes = 10
            else:
                try:
                    duration_minutes = int(raw_dur)
                except (TypeError, ValueError):
                    _json_response(handler, 400, {"error": "duration_minutes must be an integer"})
                    return
            try:
                st = _lab_manager().start(
                    gateway_ip=gw,
                    duration_minutes=duration_minutes,
                    sensors=sensors,
                )
            except ValueError as e:
                _json_response(handler, 400, {"error": str(e)})
                return
            except (FileNotFoundError, RuntimeError) as e:
                _json_response(handler, 502, {"error": str(e)})
                return
            settings = load_settings()
            settings["gateway_ip"] = gw
            save_settings(settings)
            _json_response(handler, 200, _lab_status_dict(st))
            return

        if method == "POST" and path == "/api/lab/fake-sensors/stop":
            _json_response(handler, 200, _lab_status_dict(_lab_manager().stop()))
        # --- Admin unlock + Code Signing (K16) ---
        if method == "GET" and path == "/api/admin/status":
            _json_response(handler, 200, STATE.admin.status(_admin_token(handler)))
            return

        if method == "POST" and path == "/api/admin/pin/set":
            body = _read_json(handler)
            pin = str(body.get("pin") or "")
            # First-time set is open; changing PIN requires an unlocked session.
            if STATE.admin.status()["has_pin"]:
                try:
                    STATE.admin.require(_admin_token(handler))
                except PermissionError as e:
                    _json_response(handler, 403, {"error": str(e)})
                    return
            try:
                token = STATE.admin.set_pin(pin, unlock=True)
            except ValueError as e:
                _json_response(handler, 400, {"error": str(e)})
                return
            _json_response(
                handler,
                200,
                {"ok": True, "session": token, **STATE.admin.status(token)},
            )
            return

        if method == "POST" and path == "/api/admin/unlock":
            body = _read_json(handler)
            try:
                token = STATE.admin.unlock(str(body.get("pin") or ""))
            except ValueError as e:
                _json_response(handler, 403, {"error": str(e)})
                return
            _json_response(
                handler,
                200,
                {"ok": True, "session": token, **STATE.admin.status(token)},
            )
            return

        if method == "POST" and path == "/api/admin/lock":
            STATE.admin.lock()
            _json_response(handler, 200, {"ok": True, **STATE.admin.status(None)})
            return

        if path.startswith("/api/admin/signing"):
            try:
                STATE.admin.require(_admin_token(handler))
            except PermissionError as e:
                _json_response(handler, 403, {"error": str(e)})
                return
            _handle_admin_signing(handler, method, path)
            return

        _json_response(handler, 404, {"error": f"unknown api {method} {path}"})
    except Exception as e:  # noqa: BLE001 — surface to wizard UI
        logger.exception("wizard api error")
        _json_response(handler, 500, {"error": str(e)})


def _proxy_control(
    handler: BaseHTTPRequestHandler,
    method: str,
    path: str,
    *,
    query: str = "",
) -> None:
    """Same-origin proxy to control-service; injects IOTGW_API_TOKEN when set."""
    target = f"http://{CONTROL_HOST}:{CONTROL_PORT}{path}"
    if query:
        target = f"{target}?{query}"
    headers: dict[str, str] = {}
    ctype = handler.headers.get("Content-Type")
    if ctype:
        headers["Content-Type"] = ctype
    token = os.environ.get("IOTGW_API_TOKEN", "").strip()
    if token:
        headers["X-API-Token"] = token
    body = b""
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        length = int(handler.headers.get("Content-Length") or "0")
        if length > 0:
            body = handler.rfile.read(length)
    req = urllib.request.Request(target, data=body or None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            handler.send_response(resp.status)
            ct = resp.headers.get("Content-Type", "application/json")
            handler.send_header("Content-Type", ct)
            handler.send_header("Content-Length", str(len(raw)))
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            handler.wfile.write(raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        handler.send_response(e.code)
        handler.send_header(
            "Content-Type", e.headers.get("Content-Type", "application/json")
        )
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
    except urllib.error.URLError as e:
        _json_response(
            handler,
            502,
            {"error": f"control-service unreachable: {e}"},
        )
def _handle_admin_signing(
    handler: BaseHTTPRequestHandler,
    method: str,
    path: str,
) -> None:
    """Code Signing tab APIs — require admin unlock (checked by caller).

    Trust boundary: an unlocked admin session may read/sign absolute paths on
    the local machine (intentional for build folders). ``open-export`` is
    confined to the app data dir or the last successful sign export.
    """
    from pathlib import Path

    from registry.registry import Registry  # type: ignore[import-not-found]
    from signing.service import (  # type: ignore[import-not-found]
        SignRequest,
        SigningIdentity,
        list_signable_artifacts,
        sign_artifacts,
    )
    from signing.tools import detect_signing_tools  # type: ignore[import-not-found]

    if method == "GET" and path == "/api/admin/signing/tools":
        _json_response(handler, 200, detect_signing_tools().as_dict())
        return

    if method == "GET" and path == "/api/admin/signing/identity":
        settings = load_settings()
        ident = settings.get("admin_signing") or {}
        # Never return stored passphrases
        _json_response(
            handler,
            200,
            {
                "pfx_path": ident.get("pfx_path"),
                "thumbprint": ident.get("thumbprint"),
                "gpg_key_id": ident.get("gpg_key_id"),
                "platform": _normalize_platform(ident.get("platform")),
            },
        )
        return

    if method == "POST" and path == "/api/admin/signing/identity":
        body = _read_json(handler)
        settings = load_settings()
        pfx_path = None
        if body.get("pfx_path"):
            try:
                pfx_path = _require_abs_path(str(body["pfx_path"]), label="pfx_path")
            except ValueError as e:
                _json_response(handler, 400, {"error": str(e)})
                return
        ident = {
            "platform": _normalize_platform(body.get("platform")),
            "pfx_path": pfx_path,
            "thumbprint": (
                str(body["thumbprint"]).strip() if body.get("thumbprint") else None
            ),
            "gpg_key_id": (
                str(body["gpg_key_id"]).strip() if body.get("gpg_key_id") else None
            ),
        }
        settings["admin_signing"] = ident
        save_settings(settings)
        # Optional: stash passphrases in keyring (never in settings.json)
        if body.get("pfx_passphrase"):
            keyring_store.set_secret(
                "signing-pfx", str(body["pfx_passphrase"]), kind="signing"
            )
        if body.get("gpg_passphrase"):
            keyring_store.set_secret(
                "signing-gpg", str(body["gpg_passphrase"]), kind="signing"
            )
        _json_response(handler, 200, {"ok": True, "identity": ident})
        return

    if method == "POST" and path == "/api/admin/signing/list-artifacts":
        body = _read_json(handler)
        try:
            folder = _require_abs_path(str(body.get("folder") or ""), label="folder")
        except ValueError as e:
            _json_response(handler, 400, {"error": str(e)})
            return
        try:
            items = list_signable_artifacts(Path(folder))
        except FileNotFoundError as e:
            _json_response(handler, 404, {"error": str(e)})
            return
        _json_response(handler, 200, {"artifacts": items})
        return

    if method == "POST" and path == "/api/admin/signing/sign":
        body = _read_json(handler)
        settings = load_settings()
        saved = settings.get("admin_signing") or {}

        pfx_pass = body.get("pfx_passphrase")
        if pfx_pass is None:
            pfx_pass = keyring_store.get_secret("signing-pfx", kind="signing")
        gpg_pass = body.get("gpg_passphrase")
        if gpg_pass is None:
            gpg_pass = keyring_store.get_secret("signing-gpg", kind="signing")

        pfx_path = body.get("pfx_path") or saved.get("pfx_path")
        if pfx_path:
            try:
                pfx_path = _require_abs_path(str(pfx_path), label="pfx_path")
            except ValueError as e:
                _json_response(handler, 400, {"error": str(e)})
                return

        identity = SigningIdentity(
            platform=_normalize_platform(  # type: ignore[arg-type]
                body.get("platform") or saved.get("platform") or "auto"
            ),
            pfx_path=pfx_path,
            pfx_passphrase=pfx_pass,
            thumbprint=body.get("thumbprint") or saved.get("thumbprint"),
            gpg_key_id=body.get("gpg_key_id") or saved.get("gpg_key_id"),
            gpg_passphrase=gpg_pass,
        )
        artifacts = body.get("artifacts") or []
        if not isinstance(artifacts, list) or not artifacts:
            _json_response(handler, 400, {"error": "artifacts list required"})
            return
        try:
            artifact_paths = [
                _require_abs_path(str(a), label="artifact") for a in artifacts
            ]
        except ValueError as e:
            _json_response(handler, 400, {"error": str(e)})
            return
        export_dir = body.get("export_dir")
        if export_dir:
            try:
                export_dir = _require_abs_path(str(export_dir), label="export_dir")
            except ValueError as e:
                _json_response(handler, 400, {"error": str(e)})
                return
        else:
            export_dir = str(ensure_data_dir() / "signed-export")

        result = sign_artifacts(
            SignRequest(
                artifacts=artifact_paths,
                identity=identity,
                export_dir=str(export_dir),
            )
        )
        # Persist audit via registry.sqlite (no secrets in detail)
        reg_path = ensure_data_dir() / "registry.sqlite"
        reg = Registry(str(reg_path))
        try:
            row = reg.audit(
                "code_sign" if result.ok else "code_sign_failed",
                actor="admin",
                detail=result.audit_detail,
            )
            audit_id = row.get("id")
        finally:
            reg.close()

        if result.ok and result.export_dir:
            STATE.last_signing_export = result.export_dir

        payload = result.as_dict()
        payload["audit_id"] = audit_id
        _json_response(handler, 200 if result.ok else 400, payload)
        return

    if method == "POST" and path == "/api/admin/signing/open-export":
        body = _read_json(handler)
        try:
            folder = _require_abs_path(str(body.get("folder") or ""), label="folder")
        except ValueError as e:
            _json_response(handler, 400, {"error": str(e)})
            return
        path_obj = Path(folder).resolve()
        if not path_obj.is_dir():
            _json_response(handler, 404, {"error": f"not a directory: {folder}"})
            return
        # Confine open-export to app data tree or last successful sign export.
        data_root = ensure_data_dir().resolve()
        allowed = [data_root]
        if STATE.last_signing_export:
            allowed.append(Path(STATE.last_signing_export).resolve())
        if not any(
            path_obj == root or root in path_obj.parents for root in allowed
        ):
            _json_response(
                handler,
                403,
                {
                    "error": (
                        "open-export confined to app data dir or last sign export"
                    )
                },
            )
            return
        open_in_browser(path_obj.as_uri())
        _json_response(handler, 200, {"ok": True, "folder": str(path_obj)})
        return

    _json_response(handler, 404, {"error": f"unknown api {method} {path}"})


class WizardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - " + fmt, self.address_string(), *args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/"):
            _proxy_control(self, "GET", parsed.path, query=parsed.query)
            return
        if parsed.path.startswith("/api/"):
            _handle_api(self, "GET", parsed.path, query=parsed.query)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/"):
            _proxy_control(self, "POST", parsed.path, query=parsed.query)
            return
        if parsed.path.startswith("/api/"):
            _handle_api(self, "POST", parsed.path, query=parsed.query)
            return
        self.send_error(405)

    def _serve_static(self, path: str) -> None:
        ui = desktop_ui_dir()
        if path in ("", "/"):
            path = "/splash.html"
        # Prevent path traversal
        rel = path.lstrip("/").replace("..", "")
        file_path = (ui / rel).resolve()
        if not str(file_path).startswith(str(ui.resolve())) or not file_path.is_file():
            self.send_error(404)
            return
        content_type = "text/plain"
        if file_path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif file_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(
    host: str = CONTROL_HOST,
    port: int = WIZARD_PORT,
    *,
    open_browser: bool = True,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), WizardHandler)
    url = f"http://{host}:{port}/splash.html"
    logger.info("IoT Gateway Monitor UI at %s", url)
    if open_browser:
        threading.Timer(0.5, lambda: open_in_browser(url)).start()
    return server


def run_forever(
    host: str = CONTROL_HOST,
    port: int = WIZARD_PORT,
    *,
    open_browser: bool = True,
) -> None:
    server = serve(host, port, open_browser=open_browser)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STATE.manager.stop_all()
        server.server_close()
