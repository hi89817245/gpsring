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
#define GPSRING_FIRMWARE_VERSION "v0.3.3"
#endif
#ifndef GPSRING_DEVICE_PREFIX
#define GPSRING_DEVICE_PREFIX "G0703"
#endif
#ifndef GPS_RX_PIN
#define GPS_RX_PIN 20
#endif
#ifndef GPS_TX_PIN
#define GPS_TX_PIN 21
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

static const uint32_t SERIAL_BAUD  = 115200;
static const uint32_t GPS_PROBE_MS = 15000;

// ── 龜山島預設座標（無GPS時用） ────────────────────────────
// 龜山島頭  24.845°N  121.940°E（WGS84）
// 每個 factory_id 往「上（北）」偏 10m ≈ 0.0000899°
static const double GPS_DEFAULT_LAT  = 24.845000;
static const double GPS_DEFAULT_LON  = 121.940000;
static const double GPS_OFFSET_DEG   = 0.0000899; // 10m 北偏

// ── LED 狀態機 ────────────────────────────────────────────
// ESP32-C3 內建 LED = GPIO8（低電平亮）
#ifndef LED_BUILTIN
#define LED_BUILTIN 8
#endif

enum DeviceState {
  STATE_INIT,
  STATE_GPS_SEARCHING,
  STATE_GPS_FIXED,
  STATE_CARING,
  STATE_RACING
};
DeviceState deviceState = STATE_INIT;

const char* stateLabel() {
  switch (deviceState) {
    case STATE_INIT:          return "init";
    case STATE_GPS_SEARCHING: return "gps_searching";
    case STATE_GPS_FIXED:     return "gps_fixed";
    case STATE_CARING:        return "caring";
    case STATE_RACING:        return "racing";
    default:                  return "unknown";
  }
}

// ── LED 閃爍邏輯 ──────────────────────────────────────────
// 規則：有WiFi才亮；無WiFi一律滅
// init=1閃/2s  gps_searching=2閃/2s  gps_fixed=3閃/2s
// caring=每秒4閃，停1秒  racing=5快閃/1s
struct BlinkPattern { uint8_t times; uint16_t onMs; uint16_t offMs; uint16_t pauseMs; };
static const BlinkPattern BLINK_PATTERNS[] = {
  {1, 100, 150, 1750},  // STATE_INIT
  {2, 100, 150,  900},  // STATE_GPS_SEARCHING
  {3, 100, 120,  800},  // STATE_GPS_FIXED
  {4,  80,  80, 1000},  // STATE_CARING → 4閃+停1s（省電）
  {5,  80,  80,  100},  // STATE_RACING
};

static uint32_t ledLastMs   = 0;
static uint8_t  ledBlinkIdx = 0;
static bool     ledPhaseOn  = false;

void updateLed() {
  // 無WiFi → LED 全滅
  if (WiFi.status() != WL_CONNECTED) {
    digitalWrite(LED_BUILTIN, HIGH);
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
        digitalWrite(LED_BUILTIN, LOW);   // 亮
      } else {
        ledBlinkIdx = 0;
      }
    }
  } else {
    if (elapsed >= p.onMs) {
      ledLastMs  = now;
      ledPhaseOn = false;
      ledBlinkIdx++;
      digitalWrite(LED_BUILTIN, HIGH);  // 滅
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
uint32_t factoryId  = 0;   // 全域 factory_id，每燒一片自動 +1
double   lastLat    = 0.0;
double   lastLon    = 0.0;

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

// ── 預設座標（龜山島 + factory_id 偏移）──────────────────
void getDefaultCoord(double &lat, double &lon) {
  lat = GPS_DEFAULT_LAT + GPS_OFFSET_DEG * (factoryId > 0 ? factoryId - 1 : 0);
  lon = GPS_DEFAULT_LON;
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
  json += "\"ota_partition\":\"" + String(running ? running->label : "unknown") + "\"";
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
  String html = "<html><body style='font-family:sans-serif'>";
  html += "<h1>GPSRing Factory " GPSRING_FIRMWARE_VERSION "</h1>";
  html += "<pre>" + jsonStatus() + "</pre>";
  html += "<form method='POST' action='/ota' enctype='multipart/form-data'>";
  html += "<input type='file' name='firmware'><button>OTA update</button></form>";
  html += "</body></html>";
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

void setupWiFiAndOtaWeb() {
  const char *ssid = GPSRING_WIFI_SSID;
  const char *pass = GPSRING_WIFI_PASS;
  if (!ssid || strlen(ssid) == 0) {
    Serial.println("[GPSRing][WiFi] disabled: build flag GPSRING_WIFI_SSID not set");
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, pass);
  Serial.printf("[GPSRing][WiFi] connecting ssid=%s\n", ssid);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250); Serial.print('.');
  }
  Serial.println();
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
  pinMode(LED_BUILTIN, OUTPUT);   digitalWrite(LED_BUILTIN, HIGH); // 初始滅
  analogReadResolution(12);

  prefs.begin("gpsring", false);
  bootCount = prefs.getUInt("boot_count", 0) + 1;
  prefs.putUInt("boot_count", bootCount);

  // ── factory_id：每次首次燒錄（boot_count重置時）自動 +1 ──
  // factory_id 只要存在就沿用；若 NVS 全空（新板）才 +1
  factoryId = prefs.getUInt("factory_id", 0);
  if (factoryId == 0) {
    // 透過後台取得下一個 factory_id（簡化：本機累加，可後台分配）
    // 此處用 NVS counter：每個新板首次燒錄自動累加
    factoryId = 1;  // 預設從1開始；可透過 NVS 工具手動設定
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
  deviceState = gpsFixed ? STATE_GPS_FIXED : (gpsSeen ? STATE_GPS_SEARCHING : STATE_INIT);
  printStatusLine("[GPSRing][SMOKE]");
  Serial.println("[GPSRing] JSON_STATUS " + jsonStatus());
  Serial.println("[GPSRing] Commands: STATUS, GPS, REBOOT, RACING, CARING");
  setupWiFiAndOtaWeb();
  deviceState = STATE_CARING;
}

void loop() {
  server.handleClient();
  updateLed();
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim(); cmd.toUpperCase();
    if      (cmd == "STATUS") { Serial.println("[GPSRing] JSON_STATUS " + jsonStatus()); }
    else if (cmd == "GPS") { gpsSeen=false; gpsFixed=false; lastNmea=""; probeGps(GPS_PROBE_MS); printStatusLine("[GPSRing][GPS]"); }
    else if (cmd == "REBOOT") { Serial.println("[GPSRing] rebooting"); delay(200); ESP.restart(); }
    else if (cmd == "RACING") { deviceState = STATE_RACING;  Serial.println("[GPSRing] state -> racing"); }
    else if (cmd == "CARING") { deviceState = STATE_CARING;  Serial.println("[GPSRing] state -> caring"); }
  }
  static uint32_t lastBeat = 0;
  if (millis() - lastBeat > 10000) {
    lastBeat = millis();
    printStatusLine("[GPSRing][HEARTBEAT]");
    if (WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      String url = "http://" GPSRING_OTA_HOST ":" + String(GPSRING_OTA_PORT) + "/api/v1/devices/heartbeat";
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
