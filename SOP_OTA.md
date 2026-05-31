# GPSRing SOP_OTA — MCU 燒錄與 OTA 操作規程

> 版本：v0.3.4｜更新：2026-05-31｜適用：ESP32-C3 (gpsring_factory build)

---

## A. 全新空白板（首次出廠燒錄）

### 方式 1：ESPConnect 閃存工具（推薦）— 一次燒 merged_flash.bin

1. 開啟 https://192.168.120.216（ESPConnect 頁面）
2. 點「閃存工具」→「選擇板型」= ESP32-C3
3. 上傳 **`gpsring-v0.3.4-merged-esp32c3.bin`**（位於 `\\192.168.120.228\share\esp32\`）
4. Offset 填 **`0x0`**（一次燒全區）
5. 勾「燒錄前擦除」= **否**（只擦 app 區段即可）
6. 按「燒錄」→ 等待 100% 進度條

> ✅ merged bin 已包含：bootloader(0x0) + partition_table(0x8000) + otadata(0xe000) + app(0x10000)

---

### 方式 2：ESPConnect 逐區燒錄（若 merged 失敗才用）

| 步驟 | Offset | 檔案 | 說明 |
|------|--------|------|------|
| 1 | `0x0` | `Bootloader_0x0.bin` | ESP-ROM 引導 |
| 2 | `0x8000` | `Partition_Table_0x8000.bin` | 分區表 |
| 3 | `0xE000` | `otadata_0xE000.bin` | OTA 切換標記 |
| 4 | `0x10000` | `gpsring-v0.3.4-esp32c3.bin` | 主程式 |

> ❌ 不燒：`nvs_0x9000`（WiFi設定，燒了會清除）
> ❌ 不燒：`spiffs_0x310000`（資料分區）

---

## B. 版本更新（已燒過的板子）

### 方式 1：HTTP OTA（最方便，無需接 USB）

```bash
# 板子需在同一 WiFi 且 IP 已知，從 CT218 執行
curl -X POST http://<板子IP>/ota \
  -F "firmware=@/home/hi/workspace/gpsring/firmware/gpsring-v0.3.4-esp32c3.bin"
```

> 板子會自動重啟並回傳 `OTA OK; rebooting`

**一對多批次 OTA（同 WiFi 下）：**
```bash
# 從後台取得所有在線板子 IP
curl http://192.168.120.218:8802/api/v1/devices/status | \
  python3 -c "import sys,json; [print(d['ip']) for d in json.load(sys.stdin)['devices'] if d['online']]" \
  > /tmp/device_ips.txt

# 對所有 IP 同時推送 OTA
cat /tmp/device_ips.txt | xargs -P10 -I{} \
  curl -s -X POST http://{}/ota \
    -F "firmware=@/home/hi/workspace/gpsring/firmware/gpsring-v0.3.4-esp32c3.bin" \
    -w "{}: %{http_code}\n" -o /dev/null
```

### 方式 2：ESPConnect 閃存工具（只刷 app 區）
- Offset：`0x10000`
- 檔案：`gpsring-v0.3.4-esp32c3.bin`
- **不需要**重燒 bootloader/partitions

---

## C. Build 指令（本機編譯）

```bash
cd /home/hi/workspace/gpsring

# 編譯 factory 環境（ESP32-C3）
pio run -e esp32c3_factory

# 合併全區 bin（merged_flash.bin）
python3 -m esptool --chip esp32c3 merge_bin \
  --output /tmp/gpsring-v0.3.4-merged-esp32c3.bin \
  0x0     firmware/gpsring_factory/.pio/build/esp32c3_factory/bootloader.bin \
  0x8000  firmware/gpsring_factory/.pio/build/esp32c3_factory/partitions.bin \
  0xe000  ~/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin \
  0x10000 firmware/gpsring_factory/.pio/build/esp32c3_factory/firmware.bin

# 複製到 web 靜態目錄（8802 /firmware/ URL 可下載）
cp firmware/gpsring_factory/.pio/build/esp32c3_factory/firmware.bin \
   firmware/gpsring-v0.3.4-esp32c3.bin
cp /tmp/gpsring-v0.3.4-merged-esp32c3.bin \
   firmware/gpsring-v0.3.4-merged-esp32c3.bin
```

> 下載 URL：`http://192.168.120.218:8802/firmware/gpsring-v0.3.4-esp32c3.bin`

---

## D. 燒錄驗證 SOP

燒錄完成後，用 ESPConnect 串口監視器（115200 bps）確認：

```
========== GPSRing Factory Smoke ==========
[GPSRing] firmware_version=v0.3.4 build_hash=xxxxxxxx
[GPSRing] device_id=G0703-2026-XXXXXXXX factory_id=1 mac=XXXXXXXXXXXX
[GPSRing] littlefs=OK total=983040 used=8192
[GPSRing][WiFi] ip=192.168.120.XX
[GPSRing][OTA] web updater ready: GET /status, POST /ota
[GPSRing][HEARTBEAT] POST -> 200   ← 確認後台收到
```

後台確認：
```bash
curl http://192.168.120.218:8802/api/v1/devices/status
# 應看到 "total":1, "online":1
```

---

## E. LED 狀態燈說明

| 狀態 | LED 行為 | 說明 |
|------|----------|------|
| 無WiFi | **全滅** | 省電 |
| `standby` | 每2秒閃1下 | 待機（已連WiFi，無動作）|
| `gps_searching` | 每2秒閃2下 | 搜尋GPS中 |
| `gps_fixed` | 每2秒閃3下 | GPS已定位 |
| `caring` | 每秒4快閃，停1秒 | 照護模式 |
| `racing` | 每秒5快閃 | 比賽進行中 |

> ESP32-C3 內建 LED = GPIO8（低電平亮）

---

## F. factory_id 管理

- 每個空白板首次燒錄時 NVS 自動分配 `factory_id=1`
- 可用 ESPConnect **NVS 工具** 手動設定：key=`factory_id`, type=uint32
- factory_id 用途：
  - 無GPS時，預設座標從龜山島(24.845°N, 121.940°E)往北每片偏移 10m
  - heartbeat 回報後台，方便識別每片板子

---

## G. 服務維運（start88）

```bash
# 查看服務狀態＋在線裝置
bash ~/workspace/gpsring/start88.sh status

# 重啟服務
bash ~/workspace/gpsring/start88.sh

# 停止服務
bash ~/workspace/gpsring/start88.sh stop

# 即時追蹤 log
bash ~/workspace/gpsring/start88.sh log
```

| 端點 | URL |
|------|-----|
| 公網地圖 | https://gps.xdove.win/index.html |
| API Swagger | https://gps.xdove.win/docs |
| 本地 WebUI | http://192.168.120.218:8802/index.html |
| 裝置狀態 API | http://192.168.120.218:8802/api/v1/devices/status |
| WebSocket 即時推送 | ws://192.168.120.218:8802/ws/devices |
| Firmware 下載 | http://192.168.120.218:8802/firmware/gpsring-v0.3.4-esp32c3.bin |

---

## H. Debug Checklist

- [ ] 串口有 `firmware_version=v0.3.4`？
- [ ] WiFi 連線成功（有 ip=192.168.120.XX）？
- [ ] `[HEARTBEAT] POST -> 200`？
- [ ] 後台 `/api/v1/devices/status` 顯示此板？
- [ ] GPS 未接時座標顯示龜山島（24.845°N, 121.940°E）？
- [ ] LED standby 狀態：每2秒閃1下（不是常亮）？
