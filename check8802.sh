#!/bin/bash
# check8802 快捷診斷工具

PORT_API=8801
PORT_WEB=8802

echo "=== [check8802] 鴿環測試服務狀態診斷 ==="

# 1. 檢查 API (8801)
if ss -tuln | grep -q ":${PORT_API} "; then
    PID_API=$(lsof -t -i:${PORT_API})
    echo "✅ API 服務 (Port ${PORT_API}): 在線中 (PID: ${PID_API})"
else
    echo "❌ API 服務 (Port ${PORT_API}): 離線！"
fi

# 2. 檢查 Web 前端 (8802)
if ss -tuln | grep -q ":${PORT_WEB} "; then
    PID_WEB=$(lsof -t -i:${PORT_WEB})
    echo "✅ Web 服務 (Port ${PORT_WEB}): 在線中 (PID: ${PID_WEB})"
else
    echo "❌ Web 服務 (Port ${PORT_WEB}): 離線！"
fi

# 3. 提供一鍵重啟指引
echo "--------------------------------------"
echo "💡 若需要啟動或重啟服務，請執行：start88"
echo "======================================"
