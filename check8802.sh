#!/bin/bash
# check8802 快捷診斷工具

PORT_API=8801
PORT_WEB=8802

echo "=== [check8802] 鴿環測試服務狀態診斷 ==="

# 1. 檢查 API (8801)
PID_API=$(lsof -nP -t -iTCP:${PORT_API} -sTCP:LISTEN 2>/dev/null | head -n 1)
if [ -n "${PID_API}" ]; then
    echo "✅ API 服務 (Port ${PORT_API}): 在線中 (PID: ${PID_API})"
else
    echo "❌ API 服務 (Port ${PORT_API}): 離線！"
fi

# 2. 檢查 Web 前端 (8802)
PID_WEB=$(lsof -nP -t -iTCP:${PORT_WEB} -sTCP:LISTEN 2>/dev/null | head -n 1)
if [ -n "${PID_WEB}" ]; then
    echo "✅ Web 服務 (Port ${PORT_WEB}): 在線中 (PID: ${PID_WEB})"
else
    echo "❌ Web 服務 (Port ${PORT_WEB}): 離線！"
fi

# 3. 提供一鍵重啟指引
echo "--------------------------------------"
echo "💡 若需要啟動或重啟服務，請執行：start88"
echo "======================================"
