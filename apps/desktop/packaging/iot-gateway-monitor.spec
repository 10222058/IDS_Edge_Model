# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — IoT Gateway Monitor (Windows .exe / Linux binary).

Layout inside the bundle (sys._MEIPASS):
  ui/                 Setup Wizard HTML/JS/CSS
  control-service/    Python sources + telemetry assets
  gateway/            Compose stack uploaded by the provisioner
  deploy/templates/   Promtail/etc templates
  tools/              render_config.py

Env:
  IOTGW_PYI_ONEFILE=1  (default) single-file .exe / binary
  IOTGW_PYI_ONEFILE=0  onedir (used as AppImage payload)
  IOTGW_PYI_CONSOLE=1  show console (debug)
"""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all, collect_submodules

SPECDIR = Path(SPEC).resolve().parent
DESKTOP = SPECDIR.parent
APPS = DESKTOP.parent
REPO = APPS.parent

ENTRY = str(SPECDIR / "entrypoint.py")
# Prefer multi-size .ico on Windows; fall back to PNG elsewhere.
_ICON_ICO = DESKTOP / "src-tauri" / "icons" / "icon.ico"
_ICON_PNG = DESKTOP / "src-tauri" / "icons" / "icon.png"
ICON = _ICON_ICO if _ICON_ICO.is_file() else _ICON_PNG
VERSION_INFO = SPECDIR / "windows_version_info.txt"

SKIP_DIR_NAMES = {
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "tests",
    ".local-libs",
    "build",
    "dist",
    ".git",
}


def _tree(src: Path, dest_prefix: str) -> list[tuple[str, str]]:
    """Copy files under src into the bundle, skipping caches/tests/venvs."""
    src = src.resolve()
    items: list[tuple[str, str]] = []
    if not src.is_dir():
        return items
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if any(part in SKIP_DIR_NAMES for part in rel.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        items.append((str(path), str(Path(dest_prefix) / rel.parent)))
    return items


datas = (
    _tree(DESKTOP / "ui", "ui")
    + _tree(APPS / "control-service", "control-service")
    + _tree(REPO / "gateway", "gateway")
    + _tree(REPO / "deploy", "deploy")
    + _tree(REPO / "tools", "tools")
)

hiddenimports = [
    "shell",
    "shell.__main__",
    "shell.gui",
    "shell.launcher",
    "shell.wizard_server",
    "shell.paths",
    "shell.detect",
    "shell.ssh_setup",
    "shell.admin_auth",
    "shell.keyring_store",
    "api",
    "api.__main__",
    "api.app",
    "support",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "starlette",
    "pydantic",
    "paramiko",
    "cryptography",
    "httpx",
    "keyring",
    "keyring.backends",
    "keyring.backends.fail",
    "keyring.backends.null",
    "multipart",
]

for pkg in (
    "api",
    "actions",
    "certs",
    "lab",
    "provisioner",
    "registry",
    "signing",
    "telemetry",
    "shell",
):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

binaries = []
for pkg in ("uvicorn", "fastapi", "starlette", "anyio", "httpx", "paramiko", "cryptography"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

console = os.environ.get("IOTGW_PYI_CONSOLE", "").strip().lower() in ("1", "true", "yes")
onefile = os.environ.get("IOTGW_PYI_ONEFILE", "1").strip().lower() not in ("0", "false", "no")

a = Analysis(
    [ENTRY],
    pathex=[str(DESKTOP), str(APPS / "control-service")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "playwright",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

icon_arg = str(ICON) if ICON.is_file() else None
# Embed FileDescription / ProductName so Windows shows a real app identity.
version_arg = str(VERSION_INFO) if (os.name == "nt" and VERSION_INFO.is_file()) else None

exe_common = dict(
    name="IoTGatewayMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=console,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
    version=version_arg,
)

if onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        runtime_tmpdir=None,
        **exe_common,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **exe_common,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name="IoTGatewayMonitor",
    )
