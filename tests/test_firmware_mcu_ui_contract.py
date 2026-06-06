from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_CPP = ROOT / "firmware" / "gpsring_factory" / "src" / "main.cpp"
OTG_HTML = ROOT / "otg.html"


def read_main() -> str:
    return MAIN_CPP.read_text(encoding="utf-8")


def read_otg() -> str:
    return OTG_HTML.read_text(encoding="utf-8")


def test_firmware_has_real_init_state_and_boots_to_init_before_gps_classification():
    src = read_main()
    assert "STATE_INIT" in src
    assert "case STATE_INIT" in src
    assert 'return "init"' in src
    assert "deviceState = STATE_INIT" in src
    assert "gps_fixed" in src and "gps_searching" in src


def test_firmware_separates_backend_api_host_from_mcu_ota_ui_host():
    src = read_main()
    assert "GPSRING_BACKEND_HOST" in src
    assert "GPSRING_BACKEND_PORT" in src
    assert 'String backendUrl(const char *path)' in src
    assert 'backendUrl("/api/v1/devices/heartbeat")' in src
    assert 'backendUrl("/api/v1/factory/next-id")' in src


def test_led_has_disable_override_and_ui_no_longer_promises_unreliable_direct_on_off():
    src = read_main()
    assert "ledDisabled" in src
    assert "LED_DISABLE" in src
    assert "LED_ENABLE" in src
    assert "LED disabled" in src
    assert "bootLedSelfTest" in src
    assert "0.25s ON/OFF/ON/OFF before WiFi" in src
    assert "forceLedOff" in src
    html = read_otg()
    assert "LED_DISABLE" in html
    assert "LED_ENABLE" in html
    assert "常亮" not in html
    assert "滅燈" not in html


def test_fallback_coordinate_moves_east_10m_on_every_heartbeat_without_gps_fix():
    src = read_main()
    assert "GPS_EAST_10M_DEG" in src
    assert "fallbackHeartbeatStep" in src
    assert "advanceFallbackCoordOnHeartbeat" in src
    assert "if (!gpsFixed) advanceFallbackCoordOnHeartbeat();" in src
    assert "GPS_EAST_10M_DEG * fallbackHeartbeatStep" in src
    assert "simLon_offset" not in src


def test_ringops_has_help_tooltips_and_api_quick_reference():
    html = read_otg()
    assert "Help / 說明" in html
    assert "API Quick Reference" in html
    for endpoint in ["GET /status", "POST /config", "POST /cmd", "POST /ota", "GET /api/v1/devices/status", "POST /api/v1/devices/heartbeat"]:
        assert endpoint in html
    for tooltip in ["title=\"只連接 Web Serial", "title=\"對已上線 MCU 執行 HTTP OTA", "title=\"清除 NVS"]:
        assert tooltip in html
