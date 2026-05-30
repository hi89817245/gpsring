# ESP32-C3 / C6 極簡 NVS Flash 儲存與離線軌跡快取韌體 (FIRMWARE.md)
Version: 1.1.0
Last Updated: 2026-05-24

以下是專門為**極低成本、長續航（預設 300 秒間隔，目標至少 8 小時；可依賽距調整為 10/60/180/300 秒）**設計的 Arduino C++ / ESP-IDF 韌體架構。

本韌體有三個核心省電邏輯：
1. **狀態機省電**：開機後依序進入 `init`、`caring`、`start` 三種狀態，每個狀態的 GPS 採樣、Wi-Fi/4G 發射、Deep Sleep 週期不同。
2. **Deep Sleep 保持**：每次喚醒僅工作數秒到十數秒，讀取 GPS 坐標後寫入 ESP32 內部 NVS / LittleFS / FATFS 快取，隨即關閉 GPS VCC 並進入 Deep Sleep。
3. **歸返後讀出 / 例外即時通報**：平時在海上與飛行中盡量關閉 4G/Wi-Fi 等射頻發射。歸巢插入 USB 充電座、偵測到指定 Wi-Fi、或 4G 版本遇到「擄鴿/異常滯留」事件時，才批次上傳或即時告警。

---

## 0. 韌體狀態機：init / caring / start / stop / upload

這是未來可共用的自檢方式，不論外包商韌體或我方韌體都應輸出相同狀態，方便後台快速判斷每個 GPS 鴿環是否符合公司標準。

| 狀態 | 觸發時機 | 主要行為 | 無線需求 | 後台用途 |
|---|---|---|---|---|
| `init` | 開機、配對、裝籠前 | 使用預設 `init.csv` 或 API 匯入 3 個基準座標；檢查 GPS、Flash、電池、防拆、韌體版本；必要時清除上一關資料 | 需要 Wi-Fi / USB / BLE 其一 | 確認硬體/韌體初始化正常 |
| `caring` | 配對完成到放飛前 | 每小時醒來一次，連續記錄 N 筆定位與電池狀態後深睡 | 可離線；有 Wi-Fi 時可回報心跳 | 確認裝籠期間沒有異常位移或失聯 |
| `start` | 放飛時間到歸返 | 每 n 秒正式記錄 GPS；預設 300 秒，可依賽距改 10/60/180 秒；4G 版可在異常滯留時即時通報 | 離線優先；4G 版事件式上報 | 產生正式比賽軌跡與防弊證據 |
| `stop/end` | 歸返 NFC/踏板感應、低電保護、截止時間 | 停止正式紀錄、鎖定資料 hash、標記待上傳或已結束 | 可離線；低電時不強制傳 | 防止歸返後資料繼續被改寫 |
| `upload` | NFC end 後、USB/充電座、Wi-Fi/BLE/4G 可用時 | 批次上傳，支援 partial/resume；電量 >=10% 可自動嘗試上傳 | USB/Wi-Fi/BLE/4G 其一 | 後台判斷 uploaded/partial/pending，回會可補傳 |

### init 狀態：三個預設基準點

開機時必須能拿到至少 3 個基準座標，來源可為：
1. 韌體內建預設 `init.csv`。
2. USB/ESPConnect API 寫入。
3. 後台配對流程下發。

建議欄位：

```csv
point_type,name,lat,lng,alt_m
release,放鴿點,25.835123,121.950000,0
home_cote,0703大鴻鴿會,24.685300,120.902300,80
check_anchor,北部基準檢查點,25.047800,121.517000,20
```

韌體需輸出：`state=init + firmware_version + device_id + gps_fixed + battery_mv + storage_free + init_points_hash`。

### caring 狀態：裝籠/待放飛保活

- 預設每 60 分鐘醒來一次。
- 每次醒來可連續取 3～5 筆 GPS，取中位數避免漂移。
- 若尚未放飛卻出現大位移、低電壓、防拆斷線，狀態上報 `caring_alert`。
- 完成後深睡，避免放飛前耗電。

### start 狀態：正式比賽軌跡

- 放飛時間到達後，進入正式紀錄。
- 每 n 秒寫一筆：`timestamp,lat,lng,alt,speed,hdop,satellites,battery_mv,rssi,state`。
- 離線版：歸返後 USB / Wi-Fi / BLE 批次上傳。
- 4G 版：一般仍可低頻離線，但若偵測到疑似擄鴿/異常滯留，可即時上報會長。

---

## 0.1 韌體上傳方式：.ino / .bin / ESPConnect

### `.ino` 與 `.bin` 差異

- `.ino`：Arduino 原始碼，給工程師修改、編譯、追版本。
- `.bin`：已編譯韌體，給量產、客服、鴿會現場更新用。
- `.ico`：網站 icon（圖示資產），**不是** ESP32 韌體檔，不能拿來燒錄。

### 建議流程

1. 工程師用 Arduino IDE / PlatformIO / ESP-IDF 編譯 `.ino` 或 C++ 專案。
2. 產生 `gpsring-vX.Y.Z-esp32c3.bin`。
3. **發布到可下載靜態路徑**：`.bin` 不應只留在 build 目錄，必須放到 ESPConnect/瀏覽器可直接抓取的位置。

```bash
# 將已編譯 .bin 發布到 /share/esp32，並更新 index.html / manifest.json
scripts/publish_firmware.sh path/to/gpsring-vX.Y.Z-esp32c3.bin vX.Y.Z
```

發布後可用：

| 用途 | URL |
|---|---|
| LAN 下載 | `http://192.168.120.218:8802/firmware/<file>.bin` |
| 公網/ESPConnect 下載 | `https://gps.xdove.win/firmware/<file>.bin` |
| Manifest | `https://gps.xdove.win/firmware/manifest.json` |

`/share/esp32` 是實體放檔目錄；FastAPI 8802 會以 `/firmware/` 靜態路徑公開。若 ESPConnect/OTA 工具要求 URL，優先貼 `https://gps.xdove.win/firmware/<file>.bin`。
4. 首次燒錄可用 USB：

```bash
esptool.py --chip esp32c3 --port /dev/ttyUSB0 --baud 921600 write_flash 0x0 gpsring-v0.3.0-esp32c3.bin
```

Windows 例：

```powershell
esptool.py --chip esp32c3 --port COM5 --baud 921600 write_flash 0x0 gpsring-v0.3.0-esp32c3.bin
```

4. 已安裝 ESPConnect/OTA 後，可透過瀏覽器連到裝置 AP 或管理頁，上傳 `.bin` 進行 OTA 更新。
5. OTA 成功後，裝置需在 serial console（序列監控）或 API 回報：`firmware_version`、`build_hash`、`state=init`。

> 外包要求：代工商必須交付 `.ino`/C++ source、PlatformIO 或 ESP-IDF build 設定、量產 `.bin`、分割表、燒錄位址、NVS 初始化格式，以及 OTA/ESPConnect 範例。只交 `.bin` 不足以驗收。

### 2026-05-30 ESP32-C3 到貨初測補充

目前 ESPConnect 已可成功辨識 ESP32-C3 QFN32 revision v0.4、4MB Flash、USB-JTAG/Serial，並進入 `Ready to flash`。這代表 USB 燒錄鏈路可用，但日誌顯示 `No plausible partition table entry found`，因此現階段應按「空白/未初始化板」處理：

1. 第一次正式燒錄使用 USB + 完整 factory image；不要先假設可 Wi-Fi OTA。
2. 若只拿到 app-only `.bin`，必須取得 partition table 與 app offset（常見但不保證為 `0x10000`）。
3. OTA 驗證需分兩階段：先 USB 燒入 OTA-enabled 韌體，再 OTA 小版本升級。
4. 現場 Win11 若只燒 `.bin`，不必安裝 Arduino IDE；用 ESPConnect/Edge/Chrome 即可。要改 `.ino` 或重編譯時才裝 Arduino IDE/PlatformIO。
5. 若板子插在 `192.168.11.216`，可由 SSH + `esptool.py` 代管；若板子插在 Win11，WebSerial 必須由 Win11 瀏覽器操作。

---

## 1. 完整韌體原始碼 (`firmware_nvs_cache.ino`)

```cpp
#include <Arduino.h>
#include <Preferences.h>      // 使用 Preferences 程式庫包裝 NVS，安全、防斷電損壞
#include <HardwareSerial.h>

// 1. 接腳與功耗控制定義
#define GPS_RX_PIN 20          // ESP32-C3 RX -> GP-02 TX
#define GPS_TX_PIN 21          // ESP32-C3 TX -> GP-02 RX
#define GPS_POWER_PIN 5        // 控制 GPS VCC 的 MOS 管開關 GPIO
#define SECTOR_PIN 6           // 賽鴿防拆極細導線 (觸控 / 電平偵測)

#define SLEEP_INTERVAL_SEC 180 // 180 秒 (3 分鐘) 深度睡眠喚醒一次
#define GPS_TIMEOUT_MS 15000   // 每次喚醒最長搜尋 GPS 衛星時間 (15 秒)

Preferences preferences;       // NVS 命名空間管理實例
HardwareSerial GPSSerial(1);   // 硬體串口 1 用於 GPS 數據讀取

// 儲存於 Flash 的點結構 (每點僅佔 16 Bytes，極度省空間)
struct GPSPoint {
  uint32_t timestamp;          // 時間戳記
  int32_t  lat;                // 緯度 * 1,000,000 (轉為整數儲存，防浮點數失精)
  int32_t  lng;                // 經度 * 1,000,000
  uint16_t alt;                // 高度 (公尺)
  uint8_t  speed;              // 速度 (km/h)
  uint8_t  tampered;           // 是否曾被拆卸剪斷 (防作弊標記)
};

void setup() {
  Serial.begin(115200);
  GPSSerial.begin(9600, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  // 初始化防拆引腳 (上拉電阻，若斷開則拉低或觸發中斷)
  pinMode(SECTOR_PIN, INPUT_PULLUP);
  pinMode(GPS_POWER_PIN, OUTPUT);
  
  // 喚醒時：立刻將 GPS 模組上電進行定位
  digitalWrite(GPS_POWER_PIN, HIGH);

  // 初始化 NVS 快取命名空間
  preferences.begin("gps_cache", false);

  // 讀取當前儲存的點數量與防拆標籤
  uint32_t point_count = preferences.getUInt("count", 0);
  bool is_tampered = preferences.getBool("tampered", false);

  // 檢查在睡眠期間是否有人剪斷或移動了鴿環 (防作弊核心)
  if (digitalRead(SECTOR_PIN) == LOW) {
    is_tampered = true;
    preferences.putBool("tampered", true);
    Serial.println("[SECURITY] WARNING: Leg-loop lock TAMPERED!");
  }

  Serial.printf("[SYSTEM] Boot count: %d points in NVS\n", point_count);

  // 2. 進行 GPS 定位搜星
  GPSPoint current_point;
  bool gps_fixed = get_gps_fix(&current_point);

  if (gps_fixed) {
    current_point.tampered = is_tampered ? 1 : 0;
    
    // 寫入 NVS 快取 (鍵名使用 point_0, point_1 遞增)
    char key_name[16];
    sprintf(key_name, "pt_%d", point_count);
    
    preferences.putBytes(key_name, &current_point, sizeof(GPSPoint));
    
    // 更新點計數器
    point_count++;
    preferences.putUInt("count", point_count);
    
    Serial.printf("[NVS] Saved point %d: Lat=%d, Lng=%d, Speed=%d\n", 
                  point_count, current_point.lat, current_point.lng, current_point.speed);
  } else {
    Serial.println("[GPS] FIX Failed (Timeout / Under Deck / Inside Box)");
  }

  // 3. 準備休眠，將 GPS 斷電 (省電黃金關鍵)
  digitalWrite(GPS_POWER_PIN, LOW);
  preferences.end();

  Serial.printf("[POWER] Entering Deep Sleep for %d seconds...\n", SLEEP_INTERVAL_SEC);
  
  // 設定 RTC 定時器喚醒
  esp_deep_sleep_enable_timer_wakeup(SLEEP_INTERVAL_SEC * 1000000ULL);
  
  // 開始睡眠 (此時 ESP32 功耗降至 5uA，GPS 斷電 0uA)
  esp_deep_sleep_start();
}

void loop() {
  // 深度睡眠下 setup() 執行完就睡去，loop() 永遠不會被執行
}

// 解析 NMEA 0183 經緯度核心函式
bool get_gps_fix(GPSPoint* pt) {
  uint32_t start_time = millis();
  
  while (millis() - start_time < GPS_TIMEOUT_MS) {
    if (GPSSerial.available()) {
      String sentence = GPSSerial.readStringUntil('\n');
      
      // 尋找 $GPRMC 或 $GNGGA 關鍵定位幀
      if (sentence.startsWith("$GPRMC") || sentence.startsWith("$GNRMC")) {
        // 例: $GPRMC,083559.00,A,2328.80780,N,12027.02760,E,35.4,183.2,230526,,,A*7F
        // 我們使用最精簡的逗號切分法
        int comma_indices[12];
        int comma_count = 0;
        for (int i = 0; i < sentence.length() && comma_count < 12; i++) {
          if (sentence[i] == ',') comma_indices[comma_count++] = i;
        }
        
        if (comma_count >= 8) {
          String status = sentence.substring(comma_indices[1]+1, comma_indices[2]);
          if (status == "A") { // 'A' 代表數據有效 (Active)
            String raw_lat = sentence.substring(comma_indices[2]+1, comma_indices[3]);
            String raw_lng = sentence.substring(comma_indices[4]+1, comma_indices[5]);
            String raw_speed = sentence.substring(comma_indices[6]+1, comma_indices[7]);
            
            // 轉換 NMEA DDMM.MMMM 格式為標準度數整數
            double lat_deg = parse_nmea_coord(raw_lat, sentence[comma_indices[3]+1] == 'S');
            double lng_deg = parse_nmea_coord(raw_lng, sentence[comma_indices[5]+1] == 'W');
            
            pt->lat = (int32_t)(lat_deg * 1000000.0);
            pt->lng = (int32_t)(lng_deg * 1000000.0);
            pt->speed = (uint8_t)(raw_speed.toFloat() * 1.852); // 節 轉 km/h
            pt->alt = 120; // RMC 無高度，可用預設或讀取 GGA 高度
            pt->timestamp = uint32_t(time(NULL)); // 取得 RTC 當前時間或 GPS 衛星時間
            
            return true;
          }
        }
      }
    }
  }
  return false;
}

// 輔助解析：NMEA 座標轉十進制度數 (DD.DDDD)
double parse_nmea_coord(String raw, bool is_negative) {
  if (raw.length() == 0) return 0.0;
  int dot_idx = raw.indexOf('.');
  if (dot_idx == -1) return 0.0;
  
  String deg_str = raw.substring(0, dot_idx - 2);
  String min_str = raw.substring(dot_idx - 2);
  
  double deg = deg_str.toFloat();
  double min = min_str.toFloat();
  double result = deg + (min / 60.0);
  
  return is_negative ? -result : result;
}
```

---

## 2. 插入 USB 充電讀取 API 腳本 (`read_flash_via_serial.py`)

當鴿環插回 USB 充電架時，外部 Python 腳本會向 ESP32 傳送讀取指令，一鍵獲取所有離線軌跡，並打標上載：

```python
import serial
import json
import requests

SERIAL_PORT = "/dev/ttyUSB0"  # Windows 為 COM3/COM4
BAUD_RATE = 115200
INGEST_API = "http://127.0.0.1:8801/api/v1/tracks/upload/csv"

def dump_and_upload():
    print(f"[*] 連接鴿環序列埠 {SERIAL_PORT}...")
    with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=5) as ser:
        # 發送讀取 NVS Flash 儲存點指令
        ser.write(b"DUMP_NVS_POINTS\n")
        
        points = []
        while True:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                break
            if "END_OF_DUMP" in line:
                break
            
            if line.startswith("POINT:"):
                # 格式: POINT:seq,timestamp,lat,lng,alt,speed,tampered
                parts = line.split(":")[1].split(",")
                points.append({
                    "seq": int(parts[0]),
                    "timestamp": int(parts[1]),
                    "lat": float(parts[2]) / 1000000.0,
                    "lng": float(parts[3]) / 1000000.0,
                    "alt": float(parts[4]),
                    "speed_kmh": float(parts[5]),
                    "heading": 180.0,
                    "hdop": 0.9,
                    "satellites": 12,
                    "battery_mv": 3950,
                    "rssi": -80
                })
        
        print(f"[+] 成功自離線 Flash 讀出 {len(points)} 個軌跡點！")
        
        # 轉成 CSV 上傳至大後台作弊嫌疑判定引擎
        csv_payload = "seq,timestamp,lat,lng,alt,speed_kmh,heading,hdop,satellites,battery_mv,rssi\n"
        for p in points:
            csv_payload += f"{p['seq']},{p['timestamp']},{p['lat']},{p['lng']},{p['alt']},{p['speed_kmh']},{p['heading']},{p['hdop']},{p['satellites']},{p['battery_mv']},{p['rssi']}\n"
            
        files = {'file': ('offline_track.csv', csv_payload)}
        res = requests.post(INGEST_API, files=files)
        print("[+] 判定結果:", res.json())

if __name__ == "__main__":
    # 此腳本可在鴿環插回充電座時透過 udev 觸發自動運行
    pass
```
