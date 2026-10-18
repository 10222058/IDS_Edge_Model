"""Runtime detection for the Setup Wizard (Docker optional; WSL not required)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class DockerStatus:
    present: bool
    healthy: bool
    version: str = ""
    error: str = ""
    hint: str = ""


@dataclass(frozen=True)
class WslStatus:
    on_windows: bool
    in_wsl: bool
    distro: str = ""
    wsl_ip: str = ""
    localhost_forwarding_note: str = ""


def in_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", encoding="utf-8", errors="ignore") as f:
            text = f.read().lower()
    except OSError:
        return False
    return "microsoft" in text or "wsl" in text


def operator_runtime() -> Literal["windows", "wsl", "linux"]:
    """Which operator-PC path the wizard should use."""
    if in_wsl():
        return "wsl"
    if os.name == "nt" or platform.system().lower() == "windows":
        return "windows"
    return "linux"


def detect_docker() -> DockerStatus:
    """Run ``docker info`` — healthy only when daemon responds.

    The packaged app does **not** require Docker; native Loki/Prometheus are used.
    """
    exe = shutil.which("docker")
    if not exe:
        return DockerStatus(
            present=False,
            healthy=False,
            hint=(
                "Docker is not required. The app downloads and runs Loki and "
                "Prometheus as native processes."
            ),
        )

    try:
        ver = subprocess.run(
            [exe, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        info = subprocess.run(
            [exe, "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return DockerStatus(
            present=True,
            healthy=False,
            error=str(e),
            hint="Docker CLI found but daemon did not respond.",
        )

    version = (ver.stdout or "").strip() if ver.returncode == 0 else ""
    if info.returncode != 0:
        err = (info.stderr or info.stdout or "docker info failed").strip()
        return DockerStatus(
            present=True,
            healthy=False,
            version=version,
            error=err.splitlines()[-1] if err else "docker info failed",
            hint="Start the Docker daemon / Docker Desktop, then retry.",
        )
    return DockerStatus(present=True, healthy=True, version=version or "unknown")


def _wsl_eth0_ip() -> str:
    try:
        proc = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", "eth0"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0 or not proc.stdout.strip():
        return ""
    # ``2: eth0    inet 172.28.x.x/20 ...``
    parts = proc.stdout.split()
    for i, tok in enumerate(parts):
        if tok == "inet" and i + 1 < len(parts):
            return parts[i + 1].split("/")[0]
    return ""


def detect_wsl() -> WslStatus:
    on_windows = platform.system().lower() == "windows" or os.name == "nt"
    inside = in_wsl()
    distro = os.environ.get("WSL_DISTRO_NAME", "")
    wsl_ip = _wsl_eth0_ip() if inside else ""
    note = ""
    if inside:
        note = (
            "Running inside WSL. The packaged Windows .exe does not need WSL — "
            "prefer the native Windows build for a PC without WSL."
        )
    elif on_windows:
        note = "WSL is not required. Loki and Prometheus run as native Windows processes."
    return WslStatus(
        on_windows=on_windows or inside,
        in_wsl=inside,
        distro=distro,
        wsl_ip=wsl_ip,
        localhost_forwarding_note=note,
    )


def environment_snapshot() -> dict[str, Any]:
    docker = detect_docker()
    wsl = detect_wsl()
    runtime = operator_runtime()
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "runtime": runtime,
        "docker_required": False,
        "wsl_required": False,
        "docker": asdict(docker),
        "wsl": asdict(wsl),
    }
