# 批次燒錄 SOP — GPSRing ESP32-C3

## 前提條件
- 工作目錄：`/home/hi/workspace/gpsring`
- 韌體：`firmware/gpsring-v0.3.7-merged-esp32c3.bin`（offset 0x0）
- 後台服務 8802 已運行：`./start88.sh status`
- ttyACM0 已有 hi 使用者 dialout 權限

## 流程（逐片）

### 1. 插入板子
```bash
ls /dev/ttyACM*   # 確認出現 /dev/ttyACM0
```

### 2. 取得下一個 factory_id
```bash
FID=$(curl -s http://192.168.120.218:8802/api/v1/factory/next-id | python3 -c "import sys,json; print(json.load(sys.stdin)['factory_id'])")
echo "FID=$FID"
```

### 3. Factory Flash（merged bin，含 bootloader+partition）
```bash
esptool.py --chip esp32c3 --port /dev/ttyACM0 --baud 460800 \
  write_flash --flash_mode dio --flash_freq 80m --flash_size 4MB \
  0x0 firmware/gpsring-v0.3.7-merged-esp32c3.bin
```
> 完成後板子會自動重啟

### 4. 等待板子上 WiFi（約 10~15 秒）
```bash
sleep 15
# 查看後台裝置清單，找到新上線的 MAC
curl -s http://192.168.120.218:8802/api/v1/devices/status | python3 -c "
import sys,json; d=json.load(sys.stdin)
for dev in d['devices']:
    print(dev.get('factory_id','?'), dev.get('mac'), dev.get('ip'), dev.get('firmware_version'))
"
```

### 5. 設定 factory_id 與腳環號
```bash
# 找到新板的 IP（factory_id=0 的那台）
MCU_IP=<新板IP>
RINGNO="A$(printf '%04d' $FID)"

curl -X POST http://$MCU_IP/config \
  -H 'Content-Type: application/json' \
  -d "{"factory_id":$FID,"ringno1":"$RINGNO"}"
```

### 6. 驗證
```bash
curl -s http://$MCU_IP/status | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('FID:', d.get('factory_id'), 'ringno:', d.get('ringno1'), 'fw:', d.get('firmware_version'))
"
```

### 7. 拔出，插下一片
重複步驟 1~6，factory_id 自動遞增。

---

## 一鍵腳本（批次輔助）
```bash
cd /home/hi/workspace/gpsring
bash scripts/batch_flash.sh
```
腳本會：讀 FID → flash → 等板子上線 → 自動 POST /config → 驗證 → 記錄到 `logs/batch_flash.log`

## 燒錄記錄表

| # | factory_id | MAC | ringno1 | 燒錄時間 | 備註 |
|---|---|---|---|---|---|
| 1 | 1 | B47770936314 | A0001 | 2026-05-30 | 120.82，已 OTA v0.3.7 |
| 2~9 | 待燒 | - | A0002~A0009 | - | 9片到貨，逐片燒錄 |
