#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW_DIR="$ROOT/firmware/gpsring_factory"
VERSION="${GPSRING_VERSION:-v0.3.3}"
PORT="${GPSRING_PORT:-/dev/ttyACM0}"
BAUD="${GPSRING_BAUD:-921600}"
PUBLISHED_NAME="gpsring-${VERSION}-esp32c3.bin"
PUBLISHED_PATH="/share/esp32/${PUBLISHED_NAME}"
VENV="$ROOT/.venv-pio"

usage() {
  cat <<USAGE
Usage: $0 [--no-flash] [--no-install] [--port /dev/ttyACM0]

Build -> publish /share/esp32 -> factory flash ESP32-C3 via esptool.
Env:
  GPSRING_VERSION=v0.3.3
  GPSRING_PORT=/dev/ttyACM0
  GPSRING_WIFI_SSID=... GPSRING_WIFI_PASS=...  # optional; enables firmware web OTA
USAGE
}

FLASH=1
INSTALL=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-flash) FLASH=0; shift ;;
    --no-install) INSTALL=0; shift ;;
    --port) PORT="${2:?missing port}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

cd "$ROOT"
mkdir -p /share/esp32
chmod 777 /share/esp32 2>/dev/null || true

if command -v pio >/dev/null 2>&1; then
  PIO="$(command -v pio)"
elif [[ -x "$HOME/.local/bin/pio" ]]; then
  PIO="$HOME/.local/bin/pio"
elif [[ -x "$VENV/bin/pio" ]]; then
  PIO="$VENV/bin/pio"
else
  if [[ "$INSTALL" != 1 ]]; then
    echo "[ERR] PlatformIO not found; rerun without --no-install" >&2
    exit 1
  fi
  echo "[GPSRing] installing PlatformIO into user site ~/.local/bin"
  python3 -m pip install --user --break-system-packages -U platformio
  PIO="$HOME/.local/bin/pio"
fi
EXTRA_FLAGS=()
if [[ -n "${GPSRING_WIFI_SSID:-}" ]]; then
  EXTRA_FLAGS+=("-D" "GPSRING_WIFI_SSID=\\\"${GPSRING_WIFI_SSID}\\\"")
  EXTRA_FLAGS+=("-D" "GPSRING_WIFI_PASS=\\\"${GPSRING_WIFI_PASS:-}\\\"")
fi

if [[ ${#EXTRA_FLAGS[@]} -gt 0 ]]; then
  export PLATFORMIO_BUILD_FLAGS="${EXTRA_FLAGS[*]}"
fi

echo "[GPSRing] build firmware ${VERSION}"
"$PIO" run -d "$FW_DIR"

BUILD_DIR="$FW_DIR/.pio/build/esp32c3_factory"
APP_BIN="$BUILD_DIR/firmware.bin"
BOOTLOADER_BIN="$BUILD_DIR/bootloader.bin"
PARTITIONS_BIN="$BUILD_DIR/partitions.bin"
BOOT_APP0_BIN="$(python3 - <<'PY'
from pathlib import Path
candidates = list(Path.home().glob('.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin'))
print(candidates[0] if candidates else '')
PY
)"

for f in "$APP_BIN" "$BOOTLOADER_BIN" "$PARTITIONS_BIN"; do
  [[ -s "$f" ]] || { echo "[ERR] missing build artifact: $f" >&2; exit 1; }
done
[[ -s "$BOOT_APP0_BIN" ]] || { echo "[ERR] missing boot_app0.bin under ~/.platformio" >&2; exit 1; }

cp -f "$APP_BIN" "$PUBLISHED_PATH"
sha256sum "$PUBLISHED_PATH" | tee "/share/esp32/${PUBLISHED_NAME}.sha256"
"$ROOT/scripts/publish_firmware.sh" "$PUBLISHED_PATH" "$VERSION" >/tmp/gpsring-publish-fw.log

LAN_URL="http://192.168.120.218:8802/firmware/${PUBLISHED_NAME}"
PUBLIC_URL="https://gps.xdove.win/firmware/${PUBLISHED_NAME}"
echo "[GPSRing] published app/OTA bin: $PUBLISHED_PATH"
echo "[GPSRing] LAN:    $LAN_URL"
echo "[GPSRing] Public: $PUBLIC_URL"

curl -fsS -I "$LAN_URL" | sed -n '1,8p' || true
curl -fsS -I "$PUBLIC_URL" | sed -n '1,8p' || true

if [[ "$FLASH" == 1 ]]; then
  echo "[GPSRing] preflight chip-id on $PORT"
  python3 -m esptool --chip esp32c3 --port "$PORT" chip-id
  echo "[GPSRing] factory flash: bootloader + partition + boot_app0 + app"
  python3 -m esptool --chip esp32c3 --port "$PORT" --baud "$BAUD" write_flash -z \
    0x0000 "$BOOTLOADER_BIN" \
    0x8000 "$PARTITIONS_BIN" \
    0xe000 "$BOOT_APP0_BIN" \
    0x10000 "$APP_BIN"
  echo "[GPSRing] flash done; serial smoke read 20s"
  timeout 20s python3 - <<PY || true
import serial, time
port = ${PORT@Q}
ser = serial.Serial(port, 115200, timeout=1)
ser.setDTR(False); ser.setRTS(False)
end = time.time() + 20
while time.time() < end:
    line = ser.readline().decode('utf-8', errors='replace').rstrip()
    if line:
        print(line)
ser.close()
PY
fi

SUMMARY="【GPSRing ${VERSION}｜120.218】**Factory 韌體 build/publish/flash 完成**\nbin: ${PUBLIC_URL}\nLAN: ${LAN_URL}\nport: ${PORT}"
curl -fsS -H 'Title: GPSRing factory firmware' -H 'Tags: satellite,rocket' -d "$SUMMARY" https://ntfy.xdove.win/hermes218 >/dev/null || true
