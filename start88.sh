#!/bin/bash
# start88 服務快速啟動/重啟指令
# 版本：v0.3.3  架構：單進程 8802（reverse proxy 指向此）
# gps.xdove.win  → openresty → 8802（含 API + 靜態前端）

PORT=8802
WORKDIR="/home/hi/workspace/gpsring"
VERSION="v0.3.3"
TITLE="GPS鴿環後台（單進程版）"

echo "=========================================================="
echo "      GPS 鴿環後台 [start88] - ${VERSION}  ${TITLE}"
echo "=========================================================="

# 1. 清理舊服務
echo "[*] 清理 Port 8801 / 8802 舊進程..."
kill -9 $(lsof -t -i:8801) 2>/dev/null || true
kill -9 $(lsof -t -i:8802) 2>/dev/null || true
sleep 1

# 2. 啟動單一進程（8802）—— API + 前端合一，無記憶體隔離問題
echo "[*] 啟動 8802 後台服務（API + Web）..."
cd ${WORKDIR}
nohup python3 -m uvicorn ingest_server:app --host 0.0.0.0 --port ${PORT} > /tmp/ingest_server_8802.log 2>&1 &
PID=$!

# 3. 驗證
sleep 2
echo "----------------------------------------------------------"
if ps -p $PID > /dev/null; then
    echo "✅ [8802 後台] 啟動成功 (PID: ${PID})"
else
    echo "❌ [8802 後台] 啟動失敗，請查 /tmp/ingest_server_8802.log"
fi
echo "----------------------------------------------------------"
echo "👉 端點："
echo "   - 公網地圖：https://gps.xdove.win/index.html"
echo "   - API Swagger：https://gps.xdove.win/docs"
echo "   - 本地：http://192.168.120.218:8802/index.html"
echo "   - 鴿環狀態：http://192.168.120.218:8802/api/v1/devices/status"
echo "=========================================================="

# 支援 restart 參數
if [ "$1" = "restart" ]; then
  echo "[restart] done"
fi
