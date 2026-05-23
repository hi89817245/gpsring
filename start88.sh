#!/bin/bash
# start88 服務快速啟動/重啟指令
# 版本：v0.2.1 (真實防弊沙盤 - 上傳抽稀修正版)

PORT_API=8801
PORT_WEB=8802
WORKDIR="/home/hi/workspace/gpsring"
VERSION="v0.2.1"
TITLE="真實防弊沙盤 - 上傳抽稀修正版"
echo "=========================================================="
echo "      GPS 鴿環專用後台啟動工具 [start88] - Version: ${VERSION}"
echo "      版名：「${TITLE}」"
echo "=========================================================="

# 1. 清理舊服務避免 Port 衝突
echo "[*] 正在檢查並關閉佔用 Port ${PORT_API} 與 ${PORT_WEB} 的舊服務..."
kill -9 $(lsof -t -i:${PORT_API}) 2>/dev/null || true
kill -9 $(lsof -t -i:${PORT_WEB}) 2>/dev/null || true
sleep 1

# 2. 啟動 8801 API Ingestion Server (FastAPI)
echo "[*] 啟動 8801 API 後台服務..."
cd ${WORKDIR}
nohup python3 -m uvicorn ingest_server:app --host 0.0.0.0 --port ${PORT_API} > /tmp/ingest_server_8801.log 2>&1 &
PID_API=$!

# 3. 啟動 8802 Web 前端地圖與 API 整合託管服務 (雙港合一)
echo "[*] 啟動 8802 Web 前端地圖與 API 整合託管服務 (雙港合一)..."
nohup python3 -m uvicorn ingest_server:app --host 0.0.0.0 --port ${PORT_WEB} > /tmp/ingest_server_8802.log 2>&1 &
PID_WEB=$!

# 4. 驗證服務
sleep 2
echo "----------------------------------------------------------"
if ps -p $PID_API > /dev/null; then
    echo "✅ [API 8801 服務] 啟動成功！Port: ${PORT_API} (PID: ${PID_API})"
else
    echo "❌ [API 8801 服務] 啟動失敗！請檢查 /tmp/ingest_server_8801.log"
fi

if ps -p $PID_WEB > /dev/null; then
    echo "✅ [Web 8802 整合服務] 啟動成功！Port: ${PORT_WEB} (PID: ${PID_WEB})"
else
    echo "❌ [Web 8802 整合服務] 啟動失敗！請檢查 /tmp/ingest_server_8802.log"
fi
echo "----------------------------------------------------------"
echo "👉 您的實體測試網址與外網 SSL 反代位址："
echo "   - 整合接口與地圖：https://gps.xdove.win/index.html"
echo "   - API Swagger 文件：https://gps.xdove.win/docs"
echo "   - 本地測試：http://192.168.120.218:${PORT_WEB}/index.html"
echo "=========================================================="
