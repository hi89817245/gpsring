#include <Arduino.h>
#include <Preferences.h>
#include <LittleFS.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <esp_system.h>
#include <HTTPClient.h>

#ifndef GPSRING_FIRMWARE_VERSION
#define GPSRING_FIRMWARE_VERSION "v0.3.15"
#endif
#ifndef GPSRING_DEVICE_PREFIX
#define GPSRING_DEVICE_PREFIX "G0703"
#endif
#ifndef GPS_RX_PIN
#define GPS_RX_PIN 3
#endif
#ifndef GPS_TX_PIN
#define GPS_TX_PIN 4
#endif
#ifndef GPS_POWER_PIN
#define GPS_POWER_PIN 5
#endif
#ifndef TAMPER_PIN
#define TAMPER_PIN 6
#endif
#ifndef BATTERY_ADC_PIN
#define BATTERY_ADC_PIN 0
#endif
#ifndef GPS_BAUD
#define GPS_BAUD 9600
#endif
#ifndef GPSRING_WIFI_SSID
#define GPSRING_WIFI_SSID ""
#endif
#ifndef GPSRING_WIFI_PASS
#define GPSRING_WIFI_PASS ""
#endif
#ifndef GPSRING_OTA_HOST
#define GPSRING_OTA_HOST "192.168.120.218"
#endif
#ifndef GPSRING_OTA_PORT
#define GPSRING_OTA_PORT 8801
#endif
#ifndef GPSRING_BACKEND_HOST
#define GPSRING_BACKEND_HOST "192.168.120.218"
#endif
#ifndef GPSRING_BACKEND_PORT
#define GPSRING_BACKEND_PORT 8801
#endif

static const uint32_t SERIAL_BAUD  = 115200;
static const uint32_t GPS_PROBE_MS = 15000;

// ── 龜山島預設座標（無GPS時用） ────────────────────────────
// 龜山島頭  24.845°N  121.940°E（WGS84）
// 每個 factory_id 往「上（北）」偏 10m ≈ 0.0000899°；每次 fallback heartbeat 往「右（東）」+10m
static const double GPS_DEFAULT_LAT  = 24.845000;
static const double GPS_DEFAULT_LON  = 121.940000;
static const double GPS_OFFSET_DEG   = 0.0000899; // 10m 北偏（FID 分層用）
static const double GPS_EAST_10M_DEG = 0.0000988; // 龜山島緯度附近 10m 東偏（經度）

// ── LED 狀態機 ────────────────────────────────────────────
// ESP32-C3 內建 LED = GPIO8（低電平亮）
#ifndef LED_BUILTIN
#define LED_BUILTIN 8
#endif
// LED 極性：1 = active-LOW（低電平亮，多數 ESP32-C3 內建藍燈）
//           0 = active-HIGH（高電平亮，部分外接 LED 板）
// 若燈一直亮無法滅，改為 0
#ifndef LED_ACTIVE_LOW
#define LED_ACTIVE_LOW 1
#endif
#define LED_ON  (LED_ACTIVE_LOW ? LOW  : HIGH)
#define LED_OFF (LED_ACTIVE_LOW ? HIGH : LOW)

// GPIO 診斷只掃「相對安全」的一般輸出腳：
// - 排除 GPIO18/19（USB D-/D+）、GPS_RX/TX 20/21、常見 flash SPI 12~17
// - 排除 GPIO8/9（strap/BOOT/板載 LED 高風險）與 GPIO0/2（boot strap/ADC 等常被外部電路拉動）
// 若現場要找真正的板載藍燈腳位，先用這組從肉眼/萬用表觀察；不要盲切所有 GPIO。
static const uint8_t GPIO_SWEEP_SAFE_PINS[] = {3, 4, 7, 10};
static const uint8_t GPIO_SWEEP_SAFE_COUNT = sizeof(GPIO_SWEEP_SAFE_PINS) / sizeof(GPIO_SWEEP_SAFE_PINS[0]);

// 狀態說明：
//  INIT           = 開機/配對/裝籠前初始化；即使 GPS 未定位也回報 FID fallback 龜山島座標
//  STANDBY        = 初始化後待機，等待NFC配對或上車動作
//  GPS_SEARCHING  = 已啟動 GPS 探測但尚未定位
//  GPS_FIXED      = GPS已定位
//  CARING         = NFC感應上車，進入護送模式（比賽中）
//  RACING         = 正式競飛中（鴿返計時）
enum DeviceState {
  STATE_INIT,           // 開機初始化：保留龜山島 fallback 座標供後台顯示
  STATE_STANDBY,        // 待機：WiFi已連，等NFC上車配對
  STATE_GPS_SEARCHING,
  STATE_GPS_FIXED,
  STATE_CARING,         // 上車護送（NFC感應後）
  STATE_RACING          // 競飛中
};
DeviceState deviceState = STATE_INIT;

const char* stateLabel() {
  switch (deviceState) {
    case STATE_INIT:          return "init";
    case STATE_STANDBY:       return "standby";
    case STATE_GPS_SEARCHING: return "gps_searching";
    case STATE_GPS_FIXED:     return "gps_fixed";
    case STATE_CARING:        return "caring";
    case STATE_RACING:        return "racing";
    default:                  return "unknown";
  }
}

// 非阻塞 LED 閃爍：各 state 閃爍規則（無WiFi或 ledDisabled 一律滅）
// init/standby=1閃/2s  gps_searching=2閃/2s  gps_fixed=3閃/2s
// caring=每秒4閃+停1s（省電）  racing=5快閃/1s
struct BlinkPattern { uint8_t times; uint16_t onMs; uint16_t offMs; uint16_t pauseMs; };
// onMs 加長至 300ms 以便肉眼區分閃爍次數
static const BlinkPattern BLINK_PATTERNS[] = {
  {1, 300, 200, 1500},  // STATE_INIT          → 1閃/2s
  {1, 300, 200, 1500},  // STATE_STANDBY       → 1閃/2s
  {2, 300, 200,  700},  // STATE_GPS_SEARCHING  → 2閃/2s
  {3, 300, 180,  560},  // STATE_GPS_FIXED      → 3閃/2s
  {4, 200, 150, 1000},  // STATE_CARING         → 4閃+停1s
  {5, 150, 100,  100},  // STATE_RACING         → 5快閃/1s
};

static uint32_t ledLastMs   = 0;
static uint8_t  ledBlinkIdx = 0;
static bool     ledPhaseOn  = false;
bool ledDisabled = false;

void forceLedOff() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LED_OFF);
}

// 開機 WiFi 介入前 GPIO8 藍燈自測：每秒 ON/OFF/ON/OFF，各 0.25 秒。
// 用於肉眼確認「GPIO8 本身是否受控」，不依賴 WiFi / state / ledDisabled。
void bootLedSelfTest() {
  Serial.println("[GPSRing][LED] boot self-test start: GPIO8 0.25s ON/OFF/ON/OFF before WiFi");
  for (uint8_t cycle = 0; cycle < 4; ++cycle) {
    digitalWrite(LED_BUILTIN, LED_ON);  delay(250);
    digitalWrite(LED_BUILTIN, LED_OFF); delay(250);
    digitalWrite(LED_BUILTIN, LED_ON);  delay(250);
    digitalWrite(LED_BUILTIN, LED_OFF); delay(250);
  }
  forceLedOff();
  Serial.println("[GPSRing][LED] boot self-test done; LED forced OFF before WiFi");
}

String gpioSweepSafe() {
  String out = "GPIO_SWEEP_SAFE start; pins=";
  for (uint8_t i = 0; i < GPIO_SWEEP_SAFE_COUNT; ++i) {
    if (i) out += ",";
    out += String(GPIO_SWEEP_SAFE_PINS[i]);
  }
  Serial.println("[GPSRing][GPIO] SAFE sweep start. Watch LED / meter; each pin HIGH/LOW/HIGH/LOW 250ms.");
  for (uint8_t i = 0; i < GPIO_SWEEP_SAFE_COUNT; ++i) {
    uint8_t pin = GPIO_SWEEP_SAFE_PINS[i];
    Serial.printf("[GPSRing][GPIO] test GPIO%u HIGH/LOW/HIGH/LOW\n", pin);
    pinMode(pin, OUTPUT);
    digitalWrite(pin, HIGH); delay(250);
    digitalWrite(pin, LOW);  delay(250);
    digitalWrite(pin, HIGH); delay(250);
    digitalWrite(pin, LOW);  delay(250);
    pinMode(pin, INPUT);
  }
  forceLedOff();
  Serial.println("[GPSRing][GPIO] SAFE sweep done; GPIO8 LED forced OFF.");
  out += "; done; excluded=GPIO0/2/8/9/12-21";
  return out;
}

void updateLed() {
  // ledDisabled 或 無WiFi → LED 全滅
  if (ledDisabled || WiFi.status() != WL_CONNECTED) {
    forceLedOff();
    return;
  }
  const BlinkPattern &p = BLINK_PATTERNS[deviceState];
  if (p.times == 0) return;
  uint32_t now     = millis();
  uint32_t elapsed = now - ledLastMs;
  if (!ledPhaseOn) {
    uint32_t waitMs = (ledBlinkIdx < p.times) ? p.offMs : p.pauseMs;
    if (elapsed >= waitMs) {
      ledLastMs = now;
      if (ledBlinkIdx < p.times) {
        ledPhaseOn = true;
        digitalWrite(LED_BUILTIN, LED_ON);   // 亮
      } else {
        ledBlinkIdx = 0;
      }
    }
  } else {
    if (elapsed >= p.onMs) {
      ledLastMs  = now;
      ledPhaseOn = false;
      ledBlinkIdx++;
      digitalWrite(LED_BUILTIN, LED_OFF);  // 滅
    }
  }
}

Preferences prefs;
HardwareSerial GPSSerial(1);
WebServer server(80);
String deviceId;
String lastNmea;
bool   gpsSeen   = false;
bool   gpsFixed  = false;
uint32_t bootCount  = 0;
uint32_t factoryId  = 0;
double   lastLat    = 0.0;
double   lastLon    = 0.0;
uint32_t heartbeatIntervalMs = 10000;  // 可 NVS 調整，預設 10s
String   wifiSsid   = "";
String   wifiPass   = "";
String   ringno1    = "";   // 腳環號（NVS ringno1）
String   noteText   = "";   // 備註（NVS note）
String   backendHost = GPSRING_BACKEND_HOST;
uint16_t backendPort = GPSRING_BACKEND_PORT;

String macCompact() {
  uint64_t mac = ESP.getEfuseMac();
  char buf[13];
  snprintf(buf, sizeof(buf), "%02X%02X%02X%02X%02X%02X",
           (uint8_t)(mac >> 40), (uint8_t)(mac >> 32), (uint8_t)(mac >> 24),
           (uint8_t)(mac >> 16), (uint8_t)(mac >> 8), (uint8_t)mac);
  return String(buf);
}

String buildHash() {
  String seed = String(GPSRING_FIRMWARE_VERSION) + ":" + String(__DATE__) + ":" + String(__TIME__);
  uint32_t h = 2166136261UL;
  for (size_t i = 0; i < seed.length(); ++i) { h ^= (uint8_t)seed[i]; h *= 16777619UL; }
  char buf[9]; snprintf(buf, sizeof(buf), "%08lx", (unsigned long)h);
  return String(buf);
}

String backendUrl(const char *path) {
  return String("http://") + backendHost + ":" + String(backendPort) + String(path);
}

// ── 預設座標（龜山島 + factory_id 北偏 + fallback heartbeat 東偏）────
// 無 GPS fix 時，每次 heartbeat 往右/東移動 10m，方便在地圖肉眼確認心跳有更新。
static uint32_t fallbackHeartbeatStep = 0;

void advanceFallbackCoordOnHeartbeat() {
  fallbackHeartbeatStep++;
  if (fallbackHeartbeatStep > 200) fallbackHeartbeatStep = 0;
}

void getDefaultCoord(double &lat, double &lon) {
  lat = GPS_DEFAULT_LAT + GPS_OFFSET_DEG * (factoryId > 0 ? factoryId - 1 : 0);
  lon = GPS_DEFAULT_LON + (GPS_EAST_10M_DEG * fallbackHeartbeatStep);
}

String jsonStatus() {
  const esp_partition_t *running = esp_ota_get_running_partition();
  double lat = lastLat, lon = lastLon;
  if (!gpsFixed) getDefaultCoord(lat, lon);
  String json = "{";
  json += "\"project\":\"GPSRing\",";
  json += "\"firmware_version\":\"" GPSRING_FIRMWARE_VERSION "\",";
  json += "\"build_hash\":\"" + buildHash() + "\",";
  json += "\"state\":\"" + String(stateLabel()) + "\",";
  json += "\"device_id\":\"" + deviceId + "\",";
  json += "\"factory_id\":" + String(factoryId) + ",";
  json += "\"mac\":\"" + macCompact() + "\",";
  json += "\"boot_count\":" + String(bootCount) + ",";
  json += "\"gps_seen\":" + String(gpsSeen ? "true" : "false") + ",";
  json += "\"gps_fixed\":" + String(gpsFixed ? "true" : "false") + ",";
  json += "\"lat\":" + String(lat, 6) + ",";
  json += "\"lon\":" + String(lon, 6) + ",";
  json += "\"last_nmea\":\"" + lastNmea.substring(0, 96) + "\",";
  json += "\"tamper_pin_low\":" + String(digitalRead(TAMPER_PIN) == LOW ? "true" : "false") + ",";
  json += "\"battery_raw\":" + String(analogRead(BATTERY_ADC_PIN)) + ",";
  json += "\"free_heap\":" + String(ESP.getFreeHeap()) + ",";
  json += "\"flash_size\":" + String(ESP.getFlashChipSize()) + ",";
  json += "\"littlefs_total\":" + String(LittleFS.totalBytes()) + ",";
  json += "\"littlefs_used\":" + String(LittleFS.usedBytes()) + ",";
  json += "\"ota_partition\":\"" + String(running ? running->label : "unknown") + "\",";
  json += "\"hb_interval_ms\":" + String(heartbeatIntervalMs) + ",";
  json += "\"wifi_ssid\":\"" + wifiSsid + "\",";
  json += "\"ringno1\":\"" + ringno1 + "\",";
  json += "\"note\":\"" + noteText + "\",";
  json += "\"backend_host\":\"" + backendHost + "\",";
  json += "\"backend_port\":" + String(backendPort) + ",";
  json += "\"led_disabled\":" + String(ledDisabled ? "true" : "false");
  json += "}";
  return json;
}

void printStatusLine(const char *prefix) {
  double lat = lastLat, lon = lastLon;
  if (!gpsFixed) getDefaultCoord(lat, lon);
  Serial.print(prefix);
  Serial.print(" project=GPSRing firmware_version=" GPSRING_FIRMWARE_VERSION);
  Serial.print(" build_hash="); Serial.print(buildHash());
  Serial.print(" state=");      Serial.print(stateLabel());
  Serial.print(" device_id=");  Serial.print(deviceId);
  Serial.print(" factory_id="); Serial.print(factoryId);
  Serial.print(" mac=");        Serial.print(macCompact());
  Serial.print(" boot_count="); Serial.print(bootCount);
  Serial.print(" gps_seen=");   Serial.print(gpsSeen   ? "true" : "false");
  Serial.print(" gps_fixed=");  Serial.print(gpsFixed  ? "true" : "false");
  Serial.print(" lat=");        Serial.print(lat, 6);
  Serial.print(" lon=");        Serial.print(lon, 6);
  Serial.print(" flash_size="); Serial.print(ESP.getFlashChipSize());
  Serial.print(" free_heap=");  Serial.println(ESP.getFreeHeap());
}

void probeGps(uint32_t timeoutMs) {
  Serial.printf("[GPSRing] gps_probe start rx=%d tx=%d baud=%d timeout_ms=%lu\n",
                GPS_RX_PIN, GPS_TX_PIN, GPS_BAUD, (unsigned long)timeoutMs);
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (GPSSerial.available()) {
      String line = GPSSerial.readStringUntil('\n');
      line.trim();
      if (!line.length()) continue;
      gpsSeen = true;
      lastNmea = line;
      Serial.print("[GPSRing][NMEA] "); Serial.println(line);
      if ((line.startsWith("$GNRMC") || line.startsWith("$GPRMC")) && line.indexOf(",A,") > 0) gpsFixed = true;
      if ((line.startsWith("$GNGGA") || line.startsWith("$GPGGA")) && line.indexOf(",0,") < 0) gpsFixed = true;
      if (gpsFixed) return;
    }
    delay(10);
  }
}

void handleRoot() {
  // ── 手機友善的 Web 設定頁 ──
  String ip = WiFi.localIP().toString();
  String html = R"rawhtml(<!DOCTYPE html>
<html lang='zh-Hant'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>GPSRing 設定</title>
<style>
body{font-family:sans-serif;background:#111;color:#eee;margin:0;padding:16px}
h2{color:#4af}
.card{background:#1e1e2e;border-radius:8px;padding:16px;margin:12px 0}
label{display:block;margin:8px 0 4px;font-size:0.9em;color:#aaa}
input,select{width:100%;padding:8px;border:1px solid #444;border-radius:6px;background:#2a2a3e;color:#eee;box-sizing:border-box}
.row{display:flex;gap:8px;margin-top:12px}
button{flex:1;padding:10px 12px;background:#4af;color:#111;font-weight:bold;border:none;border-radius:6px;cursor:pointer;font-size:.95em}
button.red{background:#f44}
button.grn{background:#4a4}
button.yel{background:#fa3;color:#111}
#msg{margin-top:8px;color:#4f4;font-weight:bold;font-size:.9em}
pre{font-size:0.78em;overflow:auto;background:#0d0d1a;padding:8px;border-radius:6px;max-height:220px}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.8em;background:#333;margin:2px}
.tag.ok{background:#1a3a1a;color:#4f4}
</style></head><body>
<h2>🐦 GPSRing 設定 )rawhtml";
  html += GPSRING_FIRMWARE_VERSION;
  html += R"rawhtml(</h2>
<div class='card'>
<b>裝置狀態</b><div style='font-size:.8em;color:#aaa;margin-top:4px'>init = 開機自檢與 FID fallback 座標；GPS 未定位時仍回報龜山島預設點。</div>
<div id='tags'></div>
<pre id='st'></pre>
</div>
<div class='card'>
<b title='設定出廠流水號與第一鴿環號，會寫入 NVS'>🔢 Factory ID / 腳環設定</b>
<label>Factory ID (1~99999)</label><input id='fid' type='number' min='1' max='99999' placeholder='留空不修改'>
<label>腳環號 ringno1</label><input id='rno' type='text' placeholder='例: A12345'>
<label>備註 note</label><input id='note2' type='text' placeholder='選填'>
<div class='row'>
<button type='button' onclick='saveFid()'>💾 儲存FID+腳環</button>
<button type='button' class='red' onclick='clearNvs()'>🗑 清除NVS(重置)</button>
</div>
<div id='msg2'></div>
</div>
<div class='card'>
<b title='WiFi 是 MCU 上網；heartbeat 會送到 backend_host/backend_port，不是送回 MCU 自己'>📶 WiFi / 心跳設定</b>
<label>WiFi SSID</label><input id='s' type='text' placeholder='留空=萬用字元自動'>
<label>WiFi 密碼</label><input id='p' type='password' placeholder='留空不修改'>
<label>心跳間隔 (ms, 5000~60000)</label><input id='h' type='number' min='5000' max='60000' step='1000' value='10000'>
<label>後台 Host（heartbeat/API）</label><input id='bh' type='text' placeholder='192.168.120.218'>
<label>後台 Port</label><input id='bp' type='number' min='1' max='65535' value='8801'>
<div class='row'>
<button type='button' onclick='save()'>💾 儲存並套用</button>
<button type='button' onclick='reboot()' class='yel'>🔄 重啟</button>
</div>
<div id='msg'></div>
</div>
<div class='card'>
<b>💡 LED 測試 (GPIO8 active-LOW)</b><div style='font-size:.8em;color:#aaa;margin-top:4px'>v0.3.11 開機且 WiFi 介入前會先做 GPIO8 自測：每秒 ON/OFF/ON/OFF，各 0.25 秒。若 LED_DISABLE 後仍常亮，優先判斷為板載電源燈或非 GPIO8。</div>
<div class='row'>
<button type='button' class='grn' onclick='sendSerial("STANDBY")'>待機閃</button>
<button type='button' onclick='sendSerial("CARING")'>護送閃</button>
<button type='button' class='red' onclick='sendSerial("RACING")'>競飛閃</button>
</div>
<div class='row'>
<button type='button' onclick='sendSerial("LED_DISABLE")'>🚫 LED_DISABLE</button>
<button type='button' onclick='sendSerial("LED_ENABLE")'>💡 LED_ENABLE</button>
<button type='button' onclick='sendSerial("STATUS")'>📋 STATUS</button>
</div>
<pre id='ledlog' style='max-height:80px'>-- LED log --</pre>
</div>
<div class='card'>
<b>📡 OTA 韌體更新</b>
<form method='POST' action='/ota' enctype='multipart/form-data'>
<input type='file' name='firmware' accept='.bin'>
<button style='margin-top:8px'>⬆ 上傳更新</button>
</form>
</div>
<script>
let curFid=0;
async function loadStatus(){
  try{
    const r=await fetch('/status');const j=await r.json();
    document.getElementById('st').textContent=JSON.stringify(j,null,2);
    document.getElementById('h').value=j.hb_interval_ms||10000;
    curFid=j.factory_id||0;
    if(!document.getElementById('fid').value) document.getElementById('fid').placeholder='目前: '+curFid;
    if(!document.getElementById('rno').value) document.getElementById('rno').placeholder=j.ringno1||'未設定';
    const tags=document.getElementById('tags');
    tags.innerHTML='<span class="tag">FID: <b>'+j.factory_id+'</b></span>'
      +'<span class="tag">MAC: '+j.mac+'</span>'
      +'<span class="tag '+(j.gps_fixed?'ok':'')+'">GPS: '+(j.gps_fixed?'✅已定位':'❌未定位')+'</span>'
      +'<span class="tag">State: '+j.state+'</span>';
  }catch(e){document.getElementById('st').textContent='連線失敗 '+e;}
}
async function save(){
  const body={};
  const s=document.getElementById('s').value.trim();
  const p=document.getElementById('p').value.trim();
  const h=parseInt(document.getElementById('h').value)||10000;
  const bh=document.getElementById('bh').value.trim();
  const bp=parseInt(document.getElementById('bp').value)||8801;
  if(s)body.wifi_ssid=s; if(p)body.wifi_pass=p; body.hb_interval=h;
  if(bh)body.backend_host=bh; if(bp)body.backend_port=bp;
  const r=await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  document.getElementById('msg').textContent=j.ok?'✅ WiFi/心跳/後台已儲存':'❌ 失敗';
  setTimeout(loadStatus,1200);
}
async function saveFid(){
  const fv=document.getElementById('fid').value.trim();
  const rno=document.getElementById('rno').value.trim();
  const note=document.getElementById('note2').value.trim();
  const body={};
  if(fv)body.factory_id=parseInt(fv);
  if(rno)body.ringno1=rno;
  if(note)body.note=note;
  const r=await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  document.getElementById('msg2').textContent=j.ok?'✅ FID/腳環已儲存':'❌ 失敗: '+JSON.stringify(j);
  setTimeout(loadStatus,1200);
}
async function clearNvs(){
  if(!confirm('確定清除 NVS？裝置將重置並重啟！'))return;
  await fetch('/cmd',{method:'POST',body:'CLEAR_NVS'});
  document.getElementById('msg2').textContent='⚠ NVS 已清除，重啟中…';
}
async function reboot(){
  if(!confirm('確定重啟？'))return;
  await fetch('/cmd',{method:'POST',body:'REBOOT'});
  document.getElementById('msg').textContent='🔄 重啟中…';
}
async function sendSerial(cmd){
  try{
    const r=await fetch('/cmd',{method:'POST',body:cmd});
    const t=await r.text();
    document.getElementById('ledlog').textContent+='\n> '+cmd+' => '+t;
  }catch(e){document.getElementById('ledlog').textContent+='\n[ERR] '+e;}
}
loadStatus(); setInterval(loadStatus,8000);
</script></body></html>)rawhtml";
  server.send(200, "text/html", html);
}
void handleStatus() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", jsonStatus());
}
void handleOtaUpload() {
  HTTPUpload &upload = server.upload();
  if (upload.status == UPLOAD_FILE_START) {
    Serial.printf("[GPSRing][OTA] start filename=%s\n", upload.filename.c_str());
    if (!Update.begin(UPDATE_SIZE_UNKNOWN)) Update.printError(Serial);
  } else if (upload.status == UPLOAD_FILE_WRITE) {
    if (Update.write(upload.buf, upload.currentSize) != upload.currentSize) Update.printError(Serial);
  } else if (upload.status == UPLOAD_FILE_END) {
    if (Update.end(true)) {
      Serial.printf("[GPSRing][OTA] success bytes=%u rebooting\n", upload.totalSize);
    } else { Update.printError(Serial); }
  }
}

// ── 多 SSID 萬用字元連線 ───────────────────────────────────
// 支援 prefix* 萬用字元：掃描附近 AP，依優先序嘗試匹配
// 優先順序：NVS精確SSID > 固定SSID(build flag) > 萬用字元清單
// 萬用字元格式：prefix|password  (prefix 不含 *)
struct WildcardAP { const char* prefix; const char* pass; };
static const WildcardAP WILDCARD_APS[] = {
  {"gscc",    GPSRING_WIFI_PASS},   // gscc*（主場域）
  {"gpsring", "2965084522053"},     // gpsring*（其他鴿環熱點）
  {"clock",   "2965084522053"},     // clock*（鴿鐘分享熱點）
  {nullptr, nullptr}
};

// 嘗試用萬用字元掃描連線，回傳 true=成功
bool tryWildcardWifi() {
  int n = WiFi.scanNetworks(false, false, false, 200);
  if (n <= 0) { Serial.println("[GPSRing][WiFi] scan: no networks"); return false; }
  Serial.printf("[GPSRing][WiFi] scan found %d networks\n", n);
  // 找最強訊號且符合萬用字元的 AP
  int bestIdx = -1; int bestRSSI = -999; const WildcardAP *bestWild = nullptr;
  for (int i = 0; i < n; i++) {
    String ssidFound = WiFi.SSID(i);
    for (const WildcardAP *w = WILDCARD_APS; w->prefix; w++) {
      if (ssidFound.startsWith(w->prefix)) {
        if (WiFi.RSSI(i) > bestRSSI) {
          bestRSSI = WiFi.RSSI(i); bestIdx = i; bestWild = w;
        }
      }
    }
  }
  WiFi.scanDelete();
  if (bestIdx < 0 || !bestWild) { Serial.println("[GPSRing][WiFi] no wildcard match"); return false; }
  String matchedSsid = WiFi.SSID(bestIdx);
  Serial.printf("[GPSRing][WiFi] wildcard match: %s (RSSI=%d)\n", matchedSsid.c_str(), bestRSSI);
  WiFi.begin(matchedSsid.c_str(), bestWild->pass);
  uint32_t t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < 12000) { delay(250); Serial.print('.'); }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

void setupWiFiAndOtaWeb() {
  // 優先讀 NVS 中的動態 WiFi 設定（臨時熱點用）
  // NVS key: wifi_ssid / wifi_pass（ESPConnect NVS工具可設定）
  if (wifiSsid.length() == 0) {
    prefs.begin("gpsring", true);
    wifiSsid = prefs.getString("wifi_ssid", GPSRING_WIFI_SSID);
    wifiPass = prefs.getString("wifi_pass", GPSRING_WIFI_PASS);
    heartbeatIntervalMs = prefs.getUInt("hb_interval", 10000);
    ringno1   = prefs.getString("ringno1", "");
    noteText  = prefs.getString("note", "");
    backendHost = prefs.getString("backend_host", GPSRING_BACKEND_HOST);
    backendPort = (uint16_t)prefs.getUInt("backend_port", GPSRING_BACKEND_PORT);
    ledDisabled = prefs.getBool("led_disabled", false);
    prefs.end();
  }
  WiFi.mode(WIFI_STA);

  // 1. 嘗試精確 SSID（NVS 或 build flag）
  if (wifiSsid.length() > 0) {
    WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
    Serial.printf("[GPSRing][WiFi] connecting ssid=%s\n", wifiSsid.c_str());
    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
      delay(250); Serial.print('.');
    }
    Serial.println();
  }

  // 2. 若精確 SSID 失敗，嘗試萬用字元掃描
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[GPSRing][WiFi] exact SSID failed, trying wildcard scan...");
    tryWildcardWifi();
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[GPSRing][WiFi] connect failed; OTA web disabled");
    return;
  }
  Serial.print("[GPSRing][WiFi] ip="); Serial.println(WiFi.localIP());
  server.on("/",       HTTP_GET, handleRoot);
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/ota", HTTP_POST, []() {
    server.sendHeader("Connection", "close");
    server.send(200, "text/plain", Update.hasError() ? "OTA FAIL" : "OTA OK; rebooting");
    delay(500); ESP.restart();
  }, handleOtaUpload);
  // NVS 設定端點（POST /config）
  server.on("/config", HTTP_POST, []() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    String body = server.arg("plain");
    // 支援 JSON: {"wifi_ssid":"xxx","wifi_pass":"yyy","hb_interval":30000}
    // 或 form: wifi_ssid=xxx&wifi_pass=yyy&hb_interval=30000
    auto getParam = [&](const String &key) -> String {
      // 簡單 JSON 解析
      int idx = body.indexOf("\"" + key + "\"");
      if (idx >= 0) {
        int colon = body.indexOf(":", idx);
        if (colon >= 0) {
          int q1 = body.indexOf("\"", colon + 1);
          if (q1 >= 0) {
            int q2 = body.indexOf("\"", q1 + 1);
            if (q2 > q1) return body.substring(q1 + 1, q2);
          }
          // 數字
          int end = body.indexOf(",", colon + 1);
          if (end < 0) end = body.indexOf("}", colon + 1);
          if (end > colon) { String v = body.substring(colon + 1, end); v.trim(); return v; }
        }
      }
      return server.arg(key);
    };
    prefs.begin("gpsring", false);
    String newSsid = getParam("wifi_ssid");
    String newPass = getParam("wifi_pass");
    String newHb   = getParam("hb_interval");
    String resp = "{";
    if (newSsid.length() > 0) { prefs.putString("wifi_ssid", newSsid); wifiSsid = newSsid; resp += "\"wifi_ssid\":\"" + newSsid + "\","; }
    if (newPass.length() > 0) { prefs.putString("wifi_pass", newPass); wifiPass = newPass; resp += "\"wifi_pass\":\"***\","; }
    if (newHb.length() > 0) {
      uint32_t ms = (uint32_t)newHb.toInt();
      if (ms >= 1000 && ms <= 3600000) { prefs.putUInt("hb_interval", ms); heartbeatIntervalMs = ms; }
      resp += "\"hb_interval\":" + String(heartbeatIntervalMs) + ",";
    }
    // factory_id / ringno1 / note 設定
    String newFid   = getParam("factory_id");
    String newRno   = getParam("ringno1");
    String newNote  = getParam("note");
    String newBackendHost = getParam("backend_host");
    String newBackendPort = getParam("backend_port");
    if (newFid.length() > 0) {
      uint32_t fid = (uint32_t)newFid.toInt();
      if (fid >= 1 && fid <= 99999) { prefs.putUInt("factory_id", fid); factoryId = fid; resp += "\"factory_id\":" + String(fid) + ","; }
    }
    if (newRno.length() > 0)  { prefs.putString("ringno1", newRno);  ringno1   = newRno;   resp += "\"ringno1\":\"" + newRno + "\","; }
    if (newNote.length() > 0) { prefs.putString("note", newNote);     noteText  = newNote;  resp += "\"note\":\"" + newNote + "\","; }
    prefs.end();
    resp += "\"ok\":true}";
    server.send(200, "application/json", resp);
    Serial.printf("[GPSRing][Config] updated: ssid=%s hb_interval=%lu fid=%lu\n", wifiSsid.c_str(), (unsigned long)heartbeatIntervalMs, (unsigned long)factoryId);
  });
  // /cmd — 接受串列指令（供 Web UI LED 測試、REBOOT、CLEAR_NVS 等）
  server.on("/cmd", HTTP_POST, []() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    String cmd = server.arg("plain");
    cmd.trim();
    String upper = cmd;
    upper.toUpperCase();
    String resp = "ok";
    if (upper == "REBOOT") { server.send(200, "text/plain", "rebooting"); delay(400); ESP.restart(); return; }
    else if (upper == "CLEAR_NVS") {
      prefs.begin("gpsring", false); prefs.clear(); prefs.end();
      server.send(200, "text/plain", "NVS cleared; rebooting"); delay(400); ESP.restart(); return;
    }
    else if (upper == "CARING")  { deviceState = STATE_CARING;  resp = "state->caring"; }
    else if (upper == "RACING")  { deviceState = STATE_RACING;  resp = "state->racing"; }
    else if (upper == "STANDBY") { deviceState = STATE_STANDBY; resp = "state->standby"; }
    else if (upper == "LED_DISABLE") { ledDisabled = true; prefs.begin("gpsring", false); prefs.putBool("led_disabled", true); prefs.end(); forceLedOff(); resp = "LED disabled; GPIO8 forced OFF"; }
    else if (upper == "LED_ENABLE")  { ledDisabled = false; prefs.begin("gpsring", false); prefs.putBool("led_disabled", false); prefs.end(); ledLastMs = millis(); ledBlinkIdx = 0; ledPhaseOn = false; resp = "LED enabled"; }
    else if (upper == "LED_SELFTEST") { bootLedSelfTest(); resp = "LED_SELFTEST done: GPIO8 ON/OFF/ON/OFF x4; LED forced OFF"; }
    else if (upper == "GPIO_SWEEP_SAFE") { resp = gpioSweepSafe(); }
    else if (upper == "STATUS")  { resp = jsonStatus(); }
    else { resp = "unknown cmd: " + cmd; }
    server.send(200, "text/plain", resp);
  });
  // /led — 直接控制 LED（測試 active-LOW）
  server.on("/led", HTTP_GET, []() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    String v = server.arg("v");
    if (v == "0") { ledDisabled = true; prefs.begin("gpsring", false); prefs.putBool("led_disabled", true); prefs.end(); forceLedOff(); server.send(200, "text/plain", "LED disabled; GPIO8 forced OFF"); }
    else if (v == "1") { ledDisabled = false; prefs.begin("gpsring", false); prefs.putBool("led_disabled", false); prefs.end(); ledLastMs = millis(); ledBlinkIdx = 0; ledPhaseOn = false; server.send(200, "text/plain", "LED enabled"); }
    else { server.send(400, "text/plain", "use ?v=0(disable) or ?v=1(enable)"); }
  });
  server.begin();
  Serial.println("[GPSRing][OTA] web updater ready: GET /status, POST /ota");
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  uint32_t serialStart = millis();
  while (!Serial && millis() - serialStart < 2500) delay(10);
  delay(300);

  pinMode(GPS_POWER_PIN, OUTPUT); digitalWrite(GPS_POWER_PIN, HIGH);
  pinMode(TAMPER_PIN, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);   forceLedOff(); // 初始滅
  analogReadResolution(12);

  bootLedSelfTest();

  prefs.begin("gpsring", false);
  bootCount = prefs.getUInt("boot_count", 0) + 1;
  prefs.putUInt("boot_count", bootCount);

  // 先連 WiFi，後續 factory_id 才能向後台取號；setupWiFiAndOtaWeb() 會啟動 /status /ota。
  prefs.end();
  setupWiFiAndOtaWeb();
  prefs.begin("gpsring", false);

  // ── factory_id：每次首次燒錄（boot_count重置時）自動 +1 ──
  // factory_id 只要存在就沿用；若 NVS 全空（新板）才 +1
  factoryId = prefs.getUInt("factory_id", 0);
  if (factoryId == 0) {
    // 向後台取得唯一 factory_id（確保多板不重疊）
    if (WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      String url = backendUrl("/api/v1/factory/next-id");
      http.begin(url);
      int code = http.GET();
      if (code == 200) {
        String body = http.getString();
        // 簡單解析 {"factory_id":N,...}
        int idx = body.indexOf("\"factory_id\":");
        if (idx >= 0) {
          factoryId = body.substring(idx + 13).toInt();
        }
      }
      http.end();
      Serial.printf("[GPSRing] factory_id from server=%lu (http=%d)\n", (unsigned long)factoryId, code);
    }
    if (factoryId == 0) factoryId = 1;  // 離線 fallback
    prefs.putUInt("factory_id", factoryId);
  }

  // ── device_id：G0703-[yyyymmdd]-[hhmmss] 格式（使用 buildTime 作為唯一時間戳）──
  // 使用 __DATE__ __TIME__ 和 mac 後4碼組合，確保每片唯一
  String storedId = prefs.getString("device_id", "");
  if (storedId.length() == 0) {
    // 從 buildTime 產生時間戳（格式化 __DATE__ 為 yyyymmdd）
    // __DATE__ = "May 31 2026"  __TIME__ = "04:53:12"
    char dtBuf[32];
    // 使用 mac 後8位確保每片唯一
    String mac8 = macCompact().substring(4);  // 後8碼
    snprintf(dtBuf, sizeof(dtBuf), "%s-%s-%s", GPSRING_DEVICE_PREFIX,
             String(__DATE__).substring(7).c_str(), mac8.c_str());
    // 格式: G0703-2026-70936314（年+mac）
    deviceId = String(dtBuf);
    prefs.putString("device_id", deviceId);
  } else {
    deviceId = storedId;
  }
  prefs.end();

  bool fsOk = LittleFS.begin(true);
  if (fsOk) {
    File f = LittleFS.open("/factory.txt", "w");
    if (f) {
      f.printf("GPSRing %s %s %s boot=%lu fid=%lu\n",
               GPSRING_FIRMWARE_VERSION, __DATE__, __TIME__,
               (unsigned long)bootCount, (unsigned long)factoryId);
      f.close();
    }
  }

  GPSSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  Serial.println();
  Serial.println("========== GPSRing Factory Smoke ==========");
  Serial.printf("[GPSRing] firmware_version=%s build_hash=%s\n", GPSRING_FIRMWARE_VERSION, buildHash().c_str());
  Serial.printf("[GPSRing] device_id=%s factory_id=%lu mac=%s\n",
                deviceId.c_str(), (unsigned long)factoryId, macCompact().c_str());
  Serial.printf("[GPSRing] littlefs=%s total=%u used=%u\n",
                fsOk ? "OK" : "FAIL", (unsigned)LittleFS.totalBytes(), (unsigned)LittleFS.usedBytes());
  Serial.printf("[GPSRing] tamper_pin_low=%s battery_raw=%d\n",
                digitalRead(TAMPER_PIN) == LOW ? "true" : "false", analogRead(BATTERY_ADC_PIN));

  probeGps(GPS_PROBE_MS);
  // 開機後保留 init 狀態，讓後台可穩定看到 FID fallback 龜山島座標；GPS 指令/後續狀態再切換。
  deviceState = STATE_INIT;
  printStatusLine("[GPSRing][SMOKE]");
  Serial.println("[GPSRing] JSON_STATUS " + jsonStatus());
  Serial.println("[GPSRing] Commands: STATUS, GPS, REBOOT, CARING, RACING, STANDBY");
  Serial.printf("[GPSRing] hb_interval=%lums (NVS key: hb_interval)\n", (unsigned long)heartbeatIntervalMs);
  setupWiFiAndOtaWeb();
  // 開機後維持 STANDBY；NFC感應後才進入 CARING
  // 若需測試可串口送 CARING 指令
}

void loop() {
  server.handleClient();
  updateLed();
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim(); cmd.toUpperCase();
    // 需保留原始大小寫的指令（備註/腳環號），先檢查 prefix
    String rawCmd = cmd;  // toUpperCase 後的版本
    // 對 SET_NOTE / SET_RINGNO 用原始輸入（重新讀一次前已 toUpper，故從 upper prefix 之後擷取即可）
    if      (cmd == "STATUS") { Serial.println("[GPSRing] JSON_STATUS " + jsonStatus()); }
    else if (cmd == "GPS") { gpsSeen=false; gpsFixed=false; lastNmea=""; probeGps(GPS_PROBE_MS); deviceState = gpsFixed ? STATE_GPS_FIXED : STATE_GPS_SEARCHING; printStatusLine("[GPSRing][GPS]"); }
    else if (cmd == "REBOOT")  { Serial.println("[GPSRing] rebooting"); delay(200); ESP.restart(); }
    else if (cmd == "RACING")  { deviceState = STATE_RACING;  Serial.println("[GPSRing] state -> racing"); }
    else if (cmd == "CARING")  { deviceState = STATE_CARING;  Serial.println("[GPSRing] state -> caring"); }
    else if (cmd == "STANDBY") { deviceState = STATE_STANDBY; Serial.println("[GPSRing] state -> standby"); }
    else if (cmd == "LED_DISABLE") {
      ledDisabled = true;
      prefs.begin("gpsring", false); prefs.putBool("led_disabled", true); prefs.end();
      forceLedOff();
      Serial.println("[GPSRing] LED_DISABLE OK; GPIO8 forced OFF");
    }
    else if (cmd == "LED_ENABLE") {
      ledDisabled = false;
      prefs.begin("gpsring", false); prefs.putBool("led_disabled", false); prefs.end();
      ledLastMs = millis(); ledBlinkIdx = 0; ledPhaseOn = false;
      Serial.println("[GPSRing] LED_ENABLE OK");
    }
    // ── RingOps /otg 進階指令 ──────────────────────────────────────
    else if (cmd.startsWith("SET_FID:")) {
      uint32_t fid = (uint32_t)cmd.substring(8).toInt();
      if (fid >= 1 && fid <= 99999) {
        prefs.begin("gpsring", false); prefs.putUInt("factory_id", fid); prefs.end();
        factoryId = fid;
        Serial.printf("[GPSRing] SET_FID OK factory_id=%lu\n", (unsigned long)fid);
      } else { Serial.println("[GPSRing] SET_FID ERR range 1-99999"); }
    }
    else if (cmd.startsWith("SET_WIFI:")) {
      String payload = cmd.substring(9);
      int sep = payload.indexOf('|');
      String ns = sep >= 0 ? payload.substring(0, sep) : payload;
      String np = sep >= 0 ? payload.substring(sep + 1) : "";
      prefs.begin("gpsring", false);
      if (ns.length() > 0) { prefs.putString("wifi_ssid", ns); wifiSsid = ns; }
      if (np.length() > 0) { prefs.putString("wifi_pass", np); wifiPass = np; }
      prefs.end();
      Serial.printf("[GPSRing] SET_WIFI OK ssid=%s\n", ns.c_str());
    }
    else if (cmd.startsWith("SET_RINGNO:")) {
      String rno = cmd.substring(11);
      prefs.begin("gpsring", false); prefs.putString("ringno1", rno); prefs.end();
      Serial.printf("[GPSRing] SET_RINGNO OK ringno1=%s\n", rno.c_str());
    }
    else if (cmd.startsWith("SET_NOTE:")) {
      String note = cmd.substring(9);
      prefs.begin("gpsring", false); prefs.putString("note", note); prefs.end();
      Serial.println("[GPSRing] SET_NOTE OK");
    }
    else if (cmd.startsWith("SET_HB:")) {
      uint32_t ms = (uint32_t)cmd.substring(7).toInt();
      if (ms >= 5000 && ms <= 3600000) {
        prefs.begin("gpsring", false); prefs.putUInt("hb_interval", ms); prefs.end();
        heartbeatIntervalMs = ms;
        Serial.printf("[GPSRing] SET_HB OK hb_interval=%lu\n", (unsigned long)ms);
      } else { Serial.println("[GPSRing] SET_HB ERR range 5000-3600000"); }
    }
    else if (cmd == "DUMP_GPS") {
      Serial.println("[GPSRing] JSON_GPS {\"lat\":" + String(lastLat, 6) +
        ",\"lon\":" + String(lastLon, 6) +
        ",\"gps_fixed\":" + String(gpsFixed ? "true" : "false") +
        ",\"last_nmea\":\"" + lastNmea.substring(0, 80) + "\"}");
    }
    else if (cmd == "CLEAR_NVS") {
      prefs.begin("gpsring", false); prefs.clear(); prefs.end();
      Serial.println("[GPSRing] CLEAR_NVS OK; reboot to re-register");
    }
    else if (cmd == "FACTORY_RESET") {
      prefs.begin("gpsring", false); prefs.clear(); prefs.end();
      Serial.println("[GPSRing] FACTORY_RESET OK; rebooting"); delay(300); ESP.restart();
    }
  }

  static uint32_t lastBeat = 0;
  if (millis() - lastBeat > heartbeatIntervalMs) {
    lastBeat = millis();
    if (!gpsFixed) advanceFallbackCoordOnHeartbeat();
    printStatusLine("[GPSRing][HEARTBEAT]");
    if (WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      String url = backendUrl("/api/v1/devices/heartbeat");
      http.begin(url);
      http.addHeader("Content-Type", "application/json");
      double lat = lastLat, lon = lastLon;
      if (!gpsFixed) getDefaultCoord(lat, lon);
      String body = "{";
      body += "\"device_id\":\"" + deviceId + "\",";
      body += "\"factory_id\":" + String(factoryId) + ",";
      body += "\"state\":\"" + String(stateLabel()) + "\",";
      body += "\"firmware_version\":\"" GPSRING_FIRMWARE_VERSION "\",";
      body += "\"build_hash\":\"" + buildHash() + "\",";
      body += "\"mac\":\"" + macCompact() + "\",";
      body += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
      body += "\"gps_seen\":" + String(gpsSeen ? "true" : "false") + ",";
      body += "\"gps_fixed\":" + String(gpsFixed ? "true" : "false") + ",";
      body += "\"lat\":" + String(lat, 6) + ",";
      body += "\"lon\":" + String(lon, 6) + ",";
      body += "\"boot_count\":" + String(bootCount) + ",";
      body += "\"free_heap\":" + String(ESP.getFreeHeap()) + ",";
      body += "\"battery_raw\":" + String(analogRead(BATTERY_ADC_PIN));
      body += "}";
      int code = http.POST(body);
      Serial.printf("[GPSRing][HEARTBEAT] POST -> %d\n", code);
      http.end();
    }
  }
  delay(5);
}
