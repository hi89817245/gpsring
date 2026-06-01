#!/bin/bash
# start88 服務快速啟動/重啟/狀態查看指令
# 版本：v0.3.8  架構：單進程 8802（reverse proxy 指向此）
# gps.xdove.win  → openresty → 8802（含 API + 靜態前端 + WebSocket /ws/devices）
#
# 用法：
#   ./start88.sh           → 重啟服務
#   ./start88.sh status    → 查看服務狀態
#   ./start88.sh stop      → 停止服務
#   ./start88.sh log       → 即時追蹤 log（Ctrl+C 退出）
#   ./start88.sh restart   → 同 ./start88.sh（別名）
#   ./start88.sh otg       → 開啟 RingOps 鴿環作業站網址
#   ./start88.sh mcu <ip>  → 查詢指定 MCU /status
#   ./start88.sh ota <ip>  → OTA 推送最新 v0.3.7 韌體到指定 MCU

PORT=8802
WORKDIR="/home/hi/workspace/gpsring"
VERSION="v0.3.8"
LOGFILE="/tmp/ingest_server_8802.log"
TITLE="GPS鴿環後台（單進程版）"
LATEST_FW="${WORKDIR}/firmware/gpsring-v0.3.8-esp32c3.bin"

# ── 子命令處理 ────────────────────────────────────────────
case "$1" in
  status)
    echo "═══════════════ GPSRing [start88] 狀態 ═══════════════"
    PID=$(lsof -t -i:${PORT} 2>/dev/null | head -1)
    if [ -n "$PID" ]; then
      echo "✅ 8802 服務運行中 (PID: $PID)"
      echo "   上線時間: $(ps -p $PID -o etime= 2>/dev/null | tr -d ' ')"
    else
      echo "❌ 8802 服務未運行"
    fi
    echo ""
    echo "📡 MCU 在線裝置："
    curl -s --connect-timeout 3 http://127.0.0.1:${PORT}/api/v1/devices/status 2>/dev/null \
      | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {x[\"ring_id\"]} fid={x.get(\"factory_id\",\"?\")} state={x[\"state\"]} fw={x[\"firmware_version\"]} online={x[\"online\"]}') for x in d['devices']]" 2>/dev/null \
      || echo "  (服務未回應)"
    echo ""
    echo "🌐 端點："
    echo "   公網地圖: https://gps.xdove.win/"
    echo "   RingOps: https://gps.xdove.win/otg"
    echo "   本地 8802: http://192.168.120.218:8802/"
    echo "═══════════════════════════════════════════════════════"
    exit 0
    ;;
  stop)
    echo "[stop] 停止 8802..."
    kill -9 $(lsof -t -i:${PORT}) 2>/dev/null && echo "✅ 已停止" || echo "（未在運行）"
    exit 0
    ;;
  log)
    echo "[log] 追蹤 ${LOGFILE}（Ctrl+C 退出）"
    tail -f "${LOGFILE}"
    exit 0
    ;;
  otg)
    echo "🕊️ RingOps 鴿環作業站："
    echo "   本地: http://192.168.120.218:8802/otg"
    echo "   公網: https://gps.xdove.win/otg"
    exit 0
    ;;
  mcu)
    MCU_IP="${2:-192.168.120.82}"
    echo "[mcu] 查詢 http://${MCU_IP}/status..."
    curl -s --connect-timeout 5 "http://${MCU_IP}/status" | python3 -m json.tool 2>/dev/null || echo "無回應"
    exit 0
    ;;
  ota)
    MCU_IP="${2:-192.168.120.82}"
    echo "[ota] 推送 ${LATEST_FW} → http://${MCU_IP}/ota ..."
    curl -s -X POST "http://${MCU_IP}/ota" \
      -F "firmware=@${LATEST_FW}" \
      --connect-timeout 5 -m 60 && echo "✅ OTA 推送完成" || echo "❌ OTA 失敗"
    exit 0
    ;;
esac

# ── 預設：啟動/重啟 ────────────────────────────────────────
echo "=========================================================="
echo "      GPS 鴿環後台 [start88] - ${VERSION}  ${TITLE}"
echo "=========================================================="

# 1. 清理舊服務
echo "[*] 清理 Port 8801 / 8802 舊進程..."
kill -9 $(lsof -t -i:8801) 2>/dev/null || true
kill -9 $(lsof -t -i:8802) 2>/dev/null || true
sleep 1

# 2. 啟動單一進程（8802）—— API + 前端 + WebSocket
echo "[*] 啟動 8802 後台服務（API + Web + WebSocket）..."
cd ${WORKDIR}
nohup python3 -m uvicorn ingest_server:app --host 0.0.0.0 --port ${PORT} > ${LOGFILE} 2>&1 &
PID=$!

# 3. 驗證
sleep 2
echo "----------------------------------------------------------"
if ps -p $PID > /dev/null; then
    echo "✅ [8802 後台] 啟動成功 (PID: ${PID})"
else
    echo "❌ [8802 後台] 啟動失敗，請查 ${LOGFILE}"
fi
echo "----------------------------------------------------------"
echo "👉 端點："
echo "   - 公網地圖：https://gps.xdove.win/"
echo "   - RingOps 作業站：https://gps.xdove.win/otg"
echo "   - API Swagger：https://gps.xdove.win/docs"
echo "   - 本地：http://192.168.120.218:8802/"
echo "   - 鴿環狀態：http://192.168.120.218:8802/api/v1/devices/status"
echo "   - WebSocket：ws://192.168.120.218:8802/ws/devices"
echo "   - 日誌：${LOGFILE}"
echo "=========================================================="
