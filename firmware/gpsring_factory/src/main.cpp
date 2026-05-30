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

static const uint32_t SERIAL_BAUD = 115200;
static const uint32_t GPS_PROBE_MS = 15000;

// ── LED 狀態機 ────────────────────────────────────────
// ESP32-C3 內建 LED = GPIO8（低電平亮）
#ifndef LED_BUILTIN
#define LED_BUILTIN 8
#endif

enum DeviceState { STATE_INIT, STATE_GPS_SEARCHING, STATE_GPS_FIXED, STATE_CARING, STATE_RACING };
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

// 非阻塞 LED 閃爍：每個 state 有不同 blink 次數 + 週期
// init=1閃/2s  gps_searching=2閃/2s  gps_fixed=3閃/2s  caring=長亮  racing=5閃/1s
struct BlinkPattern { uint8_t times; uint16_t onMs; uint16_t offMs; uint16_t pauseMs; };
static const BlinkPattern BLINK_PATTERNS[] = {
  {1, 100, 150, 1750},  // STATE_INIT
  {2, 100, 150,  900},  // STATE_GPS_SEARCHING
  {3, 100, 120,  800},  // STATE_GPS_FIXED
  {0,   0,   0,    0},  // STATE_CARING  → 長亮（特殊處理）
  {5,  80,  80,  100},  // STATE_RACING
};

static uint32_t ledLastMs   = 0;
static uint8_t  ledBlinkIdx = 0;
static bool     ledPhaseOn  = false;

void updateLed() {
  if (deviceState == STATE_CARING) {
    digitalWrite(LED_BUILTIN, LOW);  // 低電平亮 = 長亮
    return;
  }
  const BlinkPattern &p = BLINK_PATTERNS[deviceState];
  if (p.times == 0) return;
  uint32_t now = millis();
  uint32_t elapsed = now - ledLastMs;
  if (!ledPhaseOn) {
    // pause 或 off 狀態
    uint32_t waitMs = (ledBlinkIdx < p.times) ? p.offMs : p.pauseMs;
    if (elapsed >= waitMs) {
      ledLastMs = now;
      if (ledBlinkIdx < p.times) {
        ledPhaseOn = true;
        digitalWrite(LED_BUILTIN, LOW);  // 亮
      } else {
        ledBlinkIdx = 0;  // 重新開始一輪
      }
    }
  } else {
    if (elapsed >= p.onMs) {
      ledLastMs = now;
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
bool gpsSeen = false;
bool gpsFixed = false;
uint32_t bootCount = 0;

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
  for (size_t i = 0; i < seed.length(); ++i) {
    h ^= (uint8_t)seed[i];
    h *= 16777619UL;
  }
  char buf[9];
  snprintf(buf, sizeof(buf), "%08lx", (unsigned long)h);
  return String(buf);
}

String jsonStatus() {
  const esp_partition_t *running = esp_ota_get_running_partition();
  String json = "{";
  json += "\"project\":\"GPSRing\",";
  json += "\"firmware_version\":\"" GPSRING_FIRMWARE_VERSION "\",";
  json += "\"build_hash\":\"" + buildHash() + "\",";
  json += "\"state\":\"" + String(stateLabel()) + "\",";
  json += "\"device_id\":\"" + deviceId + "\",";
  json += "\"mac\":\"" + macCompact() + "\",";
  json += "\"boot_count\":" + String(bootCount) + ",";
  json += "\"gps_seen\":" + String(gpsSeen ? "true" : "false") + ",";
  json += "\"gps_fixed\":" + String(gpsFixed ? "true" : "false") + ",";
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
  Serial.print(prefix);
  Serial.print(" project=GPSRing");
  Serial.print(" firmware_version=" GPSRING_FIRMWARE_VERSION);
  Serial.print(" build_hash=");
  Serial.print(buildHash());
  Serial.print(" state="); Serial.print(stateLabel());
  Serial.print(" device_id=");
  Serial.print(deviceId);
  Serial.print(" mac=");
  Serial.print(macCompact());
  Serial.print(" boot_count=");
  Serial.print(bootCount);
  Serial.print(" gps_seen=");
  Serial.print(gpsSeen ? "true" : "false");
  Serial.print(" gps_fixed=");
  Serial.print(gpsFixed ? "true" : "false");
  Serial.print(" flash_size=");
  Serial.print(ESP.getFlashChipSize());
  Serial.print(" free_heap=");
  Serial.println(ESP.getFreeHeap());
}

void probeGps(uint32_t timeoutMs) {
  Serial.printf("[GPSRing] gps_probe start rx=%d tx=%d baud=%d timeout_ms=%lu\n", GPS_RX_PIN, GPS_TX_PIN, GPS_BAUD, (unsigned long)timeoutMs);
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (GPSSerial.available()) {
      String line = GPSSerial.readStringUntil('\n');
      line.trim();
      if (!line.length()) continue;
      gpsSeen = true;
      lastNmea = line;
      Serial.print("[GPSRing][NMEA] ");
      Serial.println(line);
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
    } else {
      Update.printError(Serial);
    }
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
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[GPSRing][WiFi] connect failed; OTA web disabled");
    return;
  }
  Serial.print("[GPSRing][WiFi] ip=");
  Serial.println(WiFi.localIP());
  server.on("/", HTTP_GET, handleRoot);
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/ota", HTTP_POST, []() {
    server.sendHeader("Connection", "close");
    server.send(200, "text/plain", Update.hasError() ? "OTA FAIL" : "OTA OK; rebooting");
    delay(500);
    ESP.restart();
  }, handleOtaUpload);
  server.begin();
  Serial.println("[GPSRing][OTA] web updater ready: GET /status, POST /ota");
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  uint32_t serialStart = millis();
  while (!Serial && millis() - serialStart < 2500) delay(10);
  delay(300);

  pinMode(GPS_POWER_PIN, OUTPUT);
  digitalWrite(GPS_POWER_PIN, HIGH);
  pinMode(TAMPER_PIN, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);  // 初始滅
  analogReadResolution(12);

  prefs.begin("gpsring", false);
  bootCount = prefs.getUInt("boot_count", 0) + 1;
  prefs.putUInt("boot_count", bootCount);
  deviceId = prefs.getString("device_id", String(GPSRING_DEVICE_PREFIX) + "-" + macCompact().substring(6));
  prefs.putString("device_id", deviceId);
  prefs.end();

  bool fsOk = LittleFS.begin(true);
  if (fsOk) {
    File f = LittleFS.open("/factory.txt", "w");
    if (f) {
      f.printf("GPSRing %s %s %s boot=%lu\n", GPSRING_FIRMWARE_VERSION, __DATE__, __TIME__, (unsigned long)bootCount);
      f.close();
    }
  }

  GPSSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  Serial.println();
  Serial.println("========== GPSRing Factory Smoke ==========");
  Serial.println("[GPSRing] CT218 direct USB /dev/ttyACM0 ready");
  Serial.printf("[GPSRing] firmware_version=%s build_hash=%s\n", GPSRING_FIRMWARE_VERSION, buildHash().c_str());
  Serial.printf("[GPSRing] device_id=%s mac=%s\n", deviceId.c_str(), macCompact().c_str());
  Serial.printf("[GPSRing] littlefs=%s total=%u used=%u\n", fsOk ? "OK" : "FAIL", (unsigned)LittleFS.totalBytes(), (unsigned)LittleFS.usedBytes());
  Serial.printf("[GPSRing] tamper_pin_low=%s battery_raw=%d\n", digitalRead(TAMPER_PIN) == LOW ? "true" : "false", analogRead(BATTERY_ADC_PIN));

  probeGps(GPS_PROBE_MS);
  // GPS probe 結果更新 state
  deviceState = gpsFixed ? STATE_GPS_FIXED : (gpsSeen ? STATE_GPS_SEARCHING : STATE_INIT);
  printStatusLine("[GPSRing][SMOKE]");
  Serial.println("[GPSRing] JSON_STATUS " + jsonStatus());
  Serial.println("[GPSRing] Commands: STATUS, GPS, REBOOT");
  setupWiFiAndOtaWeb();
  deviceState = STATE_CARING;  // WiFi 上線後進入 caring（等待比賽指令）
}
void loop() {
  server.handleClient();
  updateLed();
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();
    if (cmd == "STATUS") {
      Serial.println("[GPSRing] JSON_STATUS " + jsonStatus());
    } else if (cmd == "GPS") {
      gpsSeen = false;
      gpsFixed = false;
      lastNmea = "";
      probeGps(GPS_PROBE_MS);
      printStatusLine("[GPSRing][GPS]");
    } else if (cmd == "REBOOT") {
      Serial.println("[GPSRing] rebooting");
      delay(200);
      ESP.restart();
    } else if (cmd == "RACING") {
      deviceState = STATE_RACING;
      Serial.println("[GPSRing] state -> racing");
    } else if (cmd == "CARING") {
      deviceState = STATE_CARING;
      Serial.println("[GPSRing] state -> caring");
    }
  }
  static uint32_t lastBeat = 0;
  if (millis() - lastBeat > 10000) {
    lastBeat = millis();
    printStatusLine("[GPSRing][HEARTBEAT]");
    // HTTP POST heartbeat 到後台 8801
    if (WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      String url = "http://" GPSRING_OTA_HOST ":" + String(GPSRING_OTA_PORT) + "/api/v1/devices/heartbeat";
      http.begin(url);
      http.addHeader("Content-Type", "application/json");
      String body = "{";
      body += "\"device_id\":\"" + deviceId + "\",";
      body += "\"state\":\"" + String(stateLabel()) + "\",";
      body += "\"firmware_version\":\"" GPSRING_FIRMWARE_VERSION "\",";
      body += "\"build_hash\":\"" + buildHash() + "\",";
      body += "\"mac\":\"" + macCompact() + "\",";
      body += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
      body += "\"gps_seen\":" + String(gpsSeen ? "true" : "false") + ",";
      body += "\"gps_fixed\":" + String(gpsFixed ? "true" : "false") + ",";
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
