"""Launch/manage FastAPI control service (:9137) and Streamlit (:8501)."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .paths import (
    CONTROL_HOST,
    CONTROL_PORT,
    STREAMLIT_PORT,
    control_service_dir,
    data_dir,
    ensure_data_dir,
    load_settings,
    streamlit_app_dir,
)

logger = logging.getLogger(__name__)


@dataclass
class ManagedProcess:
    name: str
    proc: subprocess.Popen
    url: str


@dataclass
class ProcessManager:
    control: Optional[ManagedProcess] = None
    streamlit: Optional[ManagedProcess] = None
    _children: list[subprocess.Popen] = field(default_factory=list)

    def start_control_service(
        self,
        *,
        host: str = CONTROL_HOST,
        port: int = CONTROL_PORT,
        python: Optional[str] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> ManagedProcess:
        if self.control and self.control.proc.poll() is None:
            return self.control
        ensure_data_dir()
        cs = control_service_dir()
        py = python or sys.executable
        env = os.environ.copy()
        env["PYTHONPATH"] = str(cs) + os.pathsep + env.get("PYTHONPATH", "")
        env["IOTGW_CONTROL_HOST"] = host
        env["IOTGW_CONTROL_PORT"] = str(port)
        env.setdefault("IOTGW_DATA_DIR", str(data_dir()))
        # Frozen builds: provisioner resolves gateway/tools from this root.
        from .paths import is_frozen, repo_root

        env.setdefault("IOTGW_REPO_ROOT", str(repo_root()))
        settings = load_settings()
        # Prefer per-gateway key from settings when a single default is set.
        default_gw = settings.get("default_gateway_id")
        if default_gw and default_gw in settings.get("gateways", {}):
            gw = settings["gateways"][default_gw]
            if gw.get("ssh_key_path"):
                env.setdefault("IOTGW_SSH_KEY", gw["ssh_key_path"])
        if extra_env:
            env.update(extra_env)
        log_path = ensure_data_dir() / "control-service.log"
        log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — kept for child lifetime
        if is_frozen():
            # Same bundled exe re-entered as the control API child (no system Python).
            cmd = [py, "--run-control-service"]
            cwd = str(repo_root())
        else:
            cmd = [py, "-m", "api"]
            cwd = str(cs)
        popen_kwargs: dict = {
            "cwd": cwd,
            "env": env,
            "stdout": log_f,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            # Hide console for the child when the parent is a windowed .exe.
            # Do not use start_new_session/killpg — those are POSIX-only.
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kwargs)
        self._children.append(proc)
        url = f"http://{host}:{port}"
        self.control = ManagedProcess("control-service", proc, url)
        return self.control

    def start_streamlit(
        self,
        *,
        host: str = CONTROL_HOST,
        port: int = STREAMLIT_PORT,
        control_url: Optional[str] = None,
        python: Optional[str] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> ManagedProcess:
        if self.streamlit and self.streamlit.proc.poll() is None:
            return self.streamlit
        app_dir = streamlit_app_dir()
        app_py = app_dir / "app.py"
        if not app_py.is_file():
            raise FileNotFoundError(f"Streamlit app not found: {app_py}")
        py = python or sys.executable
        env = os.environ.copy()
        env["CONTROL_SERVICE_URL"] = control_url or f"http://{CONTROL_HOST}:{CONTROL_PORT}"
        env.setdefault("LOKI_URL", "http://127.0.0.1:3100")
        env.setdefault("PROMETHEUS_URL", "http://127.0.0.1:9090")
        if extra_env:
            env.update(extra_env)
        log_path = ensure_data_dir() / "streamlit.log"
        log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        # streamlit may be missing; surface a clear error from wait_http
        popen_kwargs: dict = {
            "cwd": str(app_dir),
            "env": env,
            "stdout": log_f,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [
                py,
                "-m",
                "streamlit",
                "run",
                str(app_py),
                "--server.address",
                host,
                "--server.port",
                str(port),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ],
            **popen_kwargs,
        )
        self._children.append(proc)
        url = f"http://{host}:{port}"
        self.streamlit = ManagedProcess("streamlit", proc, url)
        return self.streamlit

    def stop_all(self, *, grace: float = 5.0) -> None:
        for mp in (self.streamlit, self.control):
            if mp is None:
                continue
            _terminate(mp.proc, grace=grace)
        self.streamlit = None
        self.control = None
        self._children.clear()

    def status(self) -> dict:
        def _one(mp: Optional[ManagedProcess]) -> dict:
            if mp is None:
                return {"running": False}
            code = mp.proc.poll()
            return {
                "running": code is None,
                "pid": mp.proc.pid,
                "exit_code": code,
                "url": mp.url,
            }

        return {"control": _one(self.control), "streamlit": _one(self.streamlit)}


def _terminate(proc: subprocess.Popen, *, grace: float = 5.0) -> None:
    """Stop a managed child. Uses process groups on POSIX; terminate/kill on Windows."""
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt" and hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError, AttributeError):
        try:
            proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            pass
    deadline = time.time() + grace
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        if os.name != "nt" and hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError, AttributeError):
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass


def wait_http(url: str, *, timeout: float = 60.0, path: str = "") -> bool:
    target = url.rstrip("/") + path
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(target, timeout=2) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.4)
    return False


def open_in_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)
