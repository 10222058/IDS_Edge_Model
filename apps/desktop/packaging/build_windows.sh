#!/usr/bin/env bash
# Build a double-clickable Windows IoTGatewayMonitor.exe (PyInstaller onefile).
# Intended for GitHub Actions windows-latest; can also run under native Windows bash.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DESKTOP="$ROOT/apps/desktop"
PACK="$DESKTOP/packaging"
OUT="${IOTGW_DIST:-$DESKTOP/dist}"

cd "$ROOT"
python -m pip install -U pip wheel
# Pillow converts icon.png → .ico for the Windows EXE resource.
python -m pip install pyinstaller pillow \
  -r "$DESKTOP/requirements.txt" \
  -r "$ROOT/apps/control-service/requirements.txt"

export IOTGW_PYI_ONEFILE=1
export IOTGW_PYI_CONSOLE="${IOTGW_PYI_CONSOLE:-0}"

# Ensure a multi-size .ico exists for the EXE resource (SmartScreen / Explorer icon).
ICON_PNG="$DESKTOP/src-tauri/icons/icon.png"
ICON_ICO="$DESKTOP/src-tauri/icons/icon.ico"
if [[ -f "$ICON_PNG" ]] && [[ ! -f "$ICON_ICO" || "$ICON_PNG" -nt "$ICON_ICO" ]]; then
  ICON_PNG="$ICON_PNG" ICON_ICO="$ICON_ICO" python - <<'PY'
import os
from pathlib import Path
from PIL import Image

png = Path(os.environ["ICON_PNG"])
ico = Path(os.environ["ICON_ICO"])
im = Image.open(png).convert("RGBA")
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
im.resize((256, 256), Image.Resampling.LANCZOS).save(
    ico, format="ICO", sizes=sizes
)
print(f"Wrote {ico}")
PY
fi

python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$OUT" \
  --workpath "$DESKTOP/build/pyinstaller" \
  "$PACK/iot-gateway-monitor.spec"

EXE="$OUT/IoTGatewayMonitor.exe"
if [[ ! -f "$EXE" ]]; then
  echo "ERROR: expected $EXE" >&2
  ls -la "$OUT" >&2 || true
  exit 1
fi

# Checksums next to the artifact
(
  cd "$OUT"
  if command -v sha256sum >/dev/null; then
    sha256sum IoTGatewayMonitor.exe > SHA256SUMS-windows.txt
  elif command -v shasum >/dev/null; then
    shasum -a 256 IoTGatewayMonitor.exe > SHA256SUMS-windows.txt
  fi
)

echo "Built: $EXE"
ls -lh "$EXE"
