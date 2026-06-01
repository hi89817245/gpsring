#!/bin/bash
# batch_flash.sh — GPSRing ESP32-C3 批次燒錄腳本
# 用法: bash scripts/batch_flash.sh
set -e
WDIR="$(cd "$(dirname "$0")/.." && pwd)"
FW="$WDIR/firmware/gpsring-v0.3.7-merged-esp32c3.bin"
LOG="$WDIR/logs/batch_flash.log"
API="http://192.168.120.218:8802"
mkdir -p "$WDIR/logs"

echo "==============================="
echo " GPSRing 批次燒錄 SOP v0.3.7"
echo "==============================="

while true; do
  echo ""
  echo "請插入下一片 ESP32-C3 板，然後按 Enter 開始燒錄（或輸入 q 結束）"
  read -r input
  [[ "$input" == "q" ]] && echo "結束燒錄" && break

  # 確認 ttyACM0
  if [ ! -e /dev/ttyACM0 ]; then
    echo "❌ 找不到 /dev/ttyACM0，請確認板子已接上"
    continue
  fi

  # 取得 FID
  FID=$(curl -s "$API/api/v1/factory/next-id" | python3 -c "import sys,json; print(json.load(sys.stdin)['factory_id'])")
  echo "📋 分配 factory_id = $FID"
  RINGNO="A$(printf '%04d' $FID)"

  # Flash
  echo "🔥 開始燒錄..."
  esptool.py --chip esp32c3 --port /dev/ttyACM0 --baud 460800 \
    write_flash --flash_mode dio --flash_freq 80m --flash_size 4MB \
    0x0 "$FW"

  echo "⏳ 等待板子上 WiFi (15s)..."
  sleep 15

  # 找新板 IP（factory_id=0 或最新上線）
  MCU_IP=$(curl -s "$API/api/v1/devices/status" | python3 -c "
import sys,json,time
d=json.load(sys.stdin)
# 找 factory_id=0 的
for dev in d.get('devices',[]):
    if dev.get('factory_id',0)==0:
        print(dev['ip']); sys.exit(0)
# fallback: 最後上線的
devs=sorted(d.get('devices',[]), key=lambda x: x.get('last_seen',''), reverse=True)
if devs: print(devs[0]['ip'])
" 2>/dev/null)

  if [ -z "$MCU_IP" ]; then
    echo "⚠️  找不到新板 IP，請手動設定 FID=$FID"
    echo "$(date '+%Y-%m-%d %H:%M') FID=$FID RINGNO=$RINGNO IP=UNKNOWN STATUS=MANUAL" >> "$LOG"
    continue
  fi

  echo "📡 板子 IP: $MCU_IP"

  # 設定 FID + ringno
  RESP=$(curl -s -X POST "http://$MCU_IP/config" \
    -H 'Content-Type: application/json' \
    -d "{\"factory_id\":$FID,\"ringno1\":\"$RINGNO\"}")
  echo "✅ /config 回應: $RESP"

  # 驗證
  STATUS=$(curl -s "http://$MCU_IP/status")
  FW_VER=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('firmware_version','?'))")
  MAC=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mac','?'))")
  echo "🏷️  fw=$FW_VER  MAC=$MAC  FID=$FID  ringno=$RINGNO  IP=$MCU_IP"
  echo "$(date '+%Y-%m-%d %H:%M') FID=$FID RINGNO=$RINGNO MAC=$MAC IP=$MCU_IP FW=$FW_VER STATUS=OK" >> "$LOG"

  echo "🎉 完成！請拔出此片，插下一片。"
done

echo ""
echo "燒錄記錄已寫入: $LOG"
cat "$LOG"
