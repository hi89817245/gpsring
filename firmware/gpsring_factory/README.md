# GPSRing ESP32-C3 Factory Firmware

今晚實機測試用的最小 factory 韌體：

- ESP32-C3 / Arduino framework / PlatformIO CLI
- Serial 輸出 `state=init`、版本、MAC、Flash、heap、GPS NMEA 偵測狀態
- LittleFS 初始化測試
- NVS 記錄 boot count / device id
- Wi-Fi OTA Web endpoint（設定 `GPSRING_WIFI_SSID` / `GPSRING_WIFI_PASS` 後啟用）
- USB factory flash 使用 bootloader + partition + boot_app0 + app image
- OTA 更新使用發布到 `/share/esp32` 的 app-only `.bin`

## 一鍵 build / publish / flash

```bash
cd /home/hi/workspace/gpsring
scripts/build_publish_flash_factory.sh
```

輸出：

```text
/share/esp32/gpsring-v0.3.3-esp32c3.bin
https://gps.xdove.win/firmware/gpsring-v0.3.3-esp32c3.bin
```
