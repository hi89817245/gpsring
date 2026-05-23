import csv
import math
import os
import random
import time
from typing import Iterable, List, Tuple, Dict

# Demo 基準：0703 大鴻鴿會（苗栗頭份田寮永貞宮）
START_LAT = 25.835123   # 基隆外海放飛點
START_LNG = 122.014160
COTE_LAT = 24.685300
COTE_LNG = 120.902300

# 真實作弊劇本節點（近似座標；用於沙盤推演，不宣稱為精密路網資料）
KEELUNG_PICKUP = (25.1256, 121.7415, "基隆/大武崙接應點")
TOUFEN_RELEASE = (24.6930, 120.9080, "頭份交流道附近釋放")
AB_COTE = (24.8100, 121.0500, "新竹/竹北疑似 AB 中繼點")

# 高速/快速道路路廊：基隆接應 → 國一/國三南下 → 頭份交流道
HIGHWAY_CORRIDOR = [
    (25.1256, 121.7415, "基隆端/大武崙接應"),
    (25.0760, 121.5170, "台北重慶北路交流道"),
    (25.0640, 121.3620, "林口路廊"),
    (24.9910, 121.3030, "桃園路廊"),
    (24.9650, 121.2180, "中壢路廊"),
    (24.8420, 121.0110, "竹北路廊"),
    (24.7980, 121.0020, "新竹交流道"),
    (24.6930, 120.9080, "頭份交流道下車"),
]

# 高鐵路廊：台北 → 苗栗（單一合理劇本，不做不合人性的二次轉乘）
HSR_CORRIDOR = [
    (25.0478, 121.5170, "台北車站上車"),
    (25.0142, 121.4635, "板橋站通過"),
    (25.0137, 121.2149, "桃園站通過"),
    (24.8086, 121.0403, "新竹站通過"),
    (24.6053, 120.8258, "苗栗高鐵站下車"),
]

# 正常鴿性路徑：不是直線，受東北風/山線/回巢修正影響
NORMAL_WAYPOINTS = [
    (START_LAT, START_LNG, "基隆外海放飛"),
    (25.6100, 121.7700, "東北季風偏移海面段"),
    (25.3600, 121.5200, "北海岸轉內陸"),
    (25.1700, 121.3300, "林口台地西側避風"),
    (24.9800, 121.1700, "桃園丘陵修正"),
    (24.8200, 121.0200, "新竹丘陵西緣"),
    (COTE_LAT, COTE_LNG, "頭份大鴻鴿會歸返"),
]

EARTH_R = 6371000


def haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def heading_deg(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return round((math.degrees(math.atan2(y, x)) + 360) % 360, 1)


def smooth_noise(i: int, amp: float, phase: float = 0.0) -> float:
    return math.sin(i * 0.37 + phase) * amp + math.sin(i * 0.11 + phase * 0.7) * amp * 0.45


def add_point(points: List[Dict], seq: int, ts: int, lat: float, lng: float, alt: float, speed: float,
              hdop: float = 0.9, sats: int = 12, battery: int = 4050, rssi: int = -78,
              note: str = "") -> int:
    prev = (points[-1]["lat"], points[-1]["lng"]) if points else (lat, lng)
    points.append({
        "seq": seq,
        "timestamp": int(ts),
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "alt": round(alt, 1),
        "speed_kmh": round(speed, 1),
        "heading": heading_deg(prev, (lat, lng)) if points else 220.0,
        "hdop": round(hdop, 1),
        "satellites": sats,
        "battery_mv": int(battery),
        "rssi": int(rssi),
        "note": note,
    })
    return seq + 1


def interpolate_leg(points: List[Dict], seq: int, ts: int, start, end, speed_range, alt_range,
                    sample_sec: int = 60, jitter_m: float = 80, phase: float = 0.0,
                    hdop: float = 0.9, sats: int = 12, rssi: int = -78, note: str = ""):
    a = (start[0], start[1])
    b = (end[0], end[1])
    dist = haversine_m(a, b)
    speed_mid = sum(speed_range) / 2
    leg_sec = max(sample_sec, int(dist / (speed_mid / 3.6)))
    steps = max(2, int(leg_sec / sample_sec))
    for j in range(1, steps + 1):
        r = j / steps
        base_lat = a[0] + (b[0] - a[0]) * r
        base_lng = a[1] + (b[1] - a[1]) * r
        # 平滑側向漂移，正常飛行使用較大；交通路廊使用極小
        meter_lat = 1 / 111_000
        meter_lng = 1 / (111_000 * max(0.2, math.cos(math.radians(base_lat))))
        curve = smooth_noise(len(points) + j, jitter_m, phase)
        lat = base_lat + curve * meter_lat
        lng = base_lng - curve * 0.55 * meter_lng
        speed = random.uniform(*speed_range)
        alt = random.uniform(*alt_range) + smooth_noise(len(points), 18, phase)
        battery = 4050 - len(points) * 1.2
        seq = add_point(points, seq, ts, lat, lng, alt, speed, hdop, sats, battery, rssi, note)
        ts += sample_sec
    return seq, ts


def generate_normal(base_time: int):
    random.seed(70301)
    points, seq, ts = [], 1, base_time
    for i in range(len(NORMAL_WAYPOINTS) - 1):
        # 東北風：前段偏西南、後段回巢修正；速度自然起伏
        speed = (48, 82) if i < 4 else (42, 72)
        alt = (120, 330) if i < 5 else (80, 240)
        seq, ts = interpolate_leg(points, seq, ts, NORMAL_WAYPOINTS[i], NORMAL_WAYPOINTS[i + 1],
                                  speed, alt, sample_sec=60, jitter_m=260, phase=i * 0.9,
                                  hdop=0.9, sats=12, rssi=-78, note="NORMAL_PIGEON_WIND_DRIFT")
    # 歸巢降落點
    seq = add_point(points, seq, ts, COTE_LAT, COTE_LNG, 28, 0.0, 0.8, 13, 3820, -68, "ARRIVED_COTE")
    return points


def generate_highway(base_time: int):
    random.seed(70302)
    points, seq, ts = [], 1, base_time
    # 正常飛到基隆接應
    seq, ts = interpolate_leg(points, seq, ts, (START_LAT, START_LNG, "放飛"), KEELUNG_PICKUP,
                              (48, 76), (120, 280), 60, 220, 1.2, note="NORMAL_BEFORE_PICKUP")
    # 接應等待 8 分鐘（符合人性，不是長時間戲劇化滯留）
    for _ in range(8):
        lat = KEELUNG_PICKUP[0] + random.uniform(-0.00008, 0.00008)
        lng = KEELUNG_PICKUP[1] + random.uniform(-0.00008, 0.00008)
        seq = add_point(points, seq, ts, lat, lng, 42 + random.uniform(-3, 3), random.uniform(0, 1.4), 1.1, 9, 3980, -58, "GPS-R05_PICKUP_WAIT")
        ts += 60
    # 單一路徑坐車南下，貼高速路廊
    for i in range(len(HIGHWAY_CORRIDOR) - 1):
        seq, ts = interpolate_leg(points, seq, ts, HIGHWAY_CORRIDOR[i], HIGHWAY_CORRIDOR[i + 1],
                                  (82, 112), (35, 72), 60, 18, 0.2,
                                  hdop=0.8, sats=14, rssi=-62, note="GPS-R02_R03_HIGHWAY_CORRIDOR")
    # 頭份附近釋放後短距離正常歸返
    seq, ts = interpolate_leg(points, seq, ts, TOUFEN_RELEASE, (COTE_LAT, COTE_LNG, "大鴻鴿會"),
                              (38, 62), (60, 160), 60, 90, 2.1, note="RELEASE_AFTER_HIGHWAY")
    seq = add_point(points, seq, ts, COTE_LAT, COTE_LNG, 28, 0.0, 0.8, 13, 3800, -68, "ARRIVED_COTE")
    return points


def generate_ab(base_time: int):
    random.seed(70303)
    points, seq, ts = [], 1, base_time
    seq, ts = interpolate_leg(points, seq, ts, (START_LAT, START_LNG, "放飛"), AB_COTE,
                              (46, 74), (130, 300), 60, 230, 0.4, note="NORMAL_TO_AB_AREA")
    # AB 舍滯留 35 分鐘：座標穩定、速度近零
    for _ in range(35):
        seq = add_point(points, seq, ts,
                        AB_COTE[0] + random.uniform(-0.00006, 0.00006),
                        AB_COTE[1] + random.uniform(-0.00006, 0.00006),
                        48 + random.uniform(-2, 2), random.uniform(0, 1.2), 1.2, 8, 3920, -55,
                        "GPS-R05_AB_COTE_STATIONARY")
        ts += 60
    seq, ts = interpolate_leg(points, seq, ts, AB_COTE, (COTE_LAT, COTE_LNG, "大鴻鴿會"),
                              (44, 68), (90, 220), 60, 150, 1.8, note="NORMAL_AFTER_AB_RELEASE")
    seq = add_point(points, seq, ts, COTE_LAT, COTE_LNG, 28, 0.0, 0.8, 13, 3800, -68, "ARRIVED_COTE")
    return points


def generate_hsr(base_time: int):
    random.seed(70304)
    points, seq, ts = [], 1, base_time
    # 前段接近台北車站，避免幻想多次轉乘；只有一段 HSR 劇本
    seq, ts = interpolate_leg(points, seq, ts, (START_LAT, START_LNG, "放飛"), HSR_CORRIDOR[0],
                              (48, 76), (130, 290), 60, 210, 1.5, note="NORMAL_TO_TAIPEI_HSR")
    # 進站等待 10 分鐘
    for _ in range(10):
        seq = add_point(points, seq, ts,
                        HSR_CORRIDOR[0][0] + random.uniform(-0.00004, 0.00004),
                        HSR_CORRIDOR[0][1] + random.uniform(-0.00004, 0.00004),
                        35 + random.uniform(-2, 2), random.uniform(0, 0.8), 0.9, 13, 3960, -64,
                        "GPS-R05_TAIPEI_STATION_WAIT")
        ts += 60
    # 台北 → 苗栗高鐵單一路廊
    for i in range(len(HSR_CORRIDOR) - 1):
        seq, ts = interpolate_leg(points, seq, ts, HSR_CORRIDOR[i], HSR_CORRIDOR[i + 1],
                                  (185, 292), (30, 65), 60, 10, 0.0,
                                  hdop=0.7, sats=15, rssi=-72, note="GPS-R04_HSR_TAIPEI_TO_MIAOLI")
    # 苗栗高鐵站下車，短距離釋放回頭份
    seq, ts = interpolate_leg(points, seq, ts, HSR_CORRIDOR[-1], (COTE_LAT, COTE_LNG, "大鴻鴿會"),
                              (38, 64), (70, 170), 60, 100, 2.4, note="RELEASE_AFTER_HSR_MIAOLI")
    seq = add_point(points, seq, ts, COTE_LAT, COTE_LNG, 28, 0.0, 0.8, 13, 3790, -69, "ARRIVED_COTE")
    return points


def generate_gps_fault(base_time: int):
    random.seed(70305)
    points = generate_normal(base_time)
    # 製造幾個 GPS 漂移跳點：高 HDOP / 低衛星，不直接視為作弊
    for idx in (18, 19, 45):
        if idx < len(points):
            points[idx]["lat"] += random.uniform(0.018, 0.032)
            points[idx]["lng"] += random.uniform(-0.026, -0.014)
            points[idx]["hdop"] = 4.8
            points[idx]["satellites"] = 4
            points[idx]["note"] = "GPS-R06_SIGNAL_DRIFT"
    return points


def generate_csv_track(mode="normal"):
    base_time = int(time.time()) - 8 * 3600
    aliases = {"cheat": "cheat_highway", "highway": "cheat_highway", "hsr": "cheat_hsr", "ab": "suspicious_ab"}
    mode = aliases.get(mode, mode)
    if mode == "normal":
        return generate_normal(base_time)
    if mode == "cheat_highway":
        return generate_highway(base_time)
    if mode == "suspicious_ab":
        return generate_ab(base_time)
    if mode == "cheat_hsr":
        return generate_hsr(base_time)
    if mode == "gps_fault":
        return generate_gps_fault(base_time)
    return generate_normal(base_time)


def save_to_csv(filename, points, ring_id="G0703-00001"):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "seq", "timestamp", "lat", "lng", "alt", "speed_kmh", "heading", "hdop", "satellites", "battery_mv", "rssi"])
        for p in points:
            writer.writerow([
                ring_id, p["seq"], p["timestamp"], p["lat"], p["lng"], p["alt"],
                p["speed_kmh"], p["heading"], p["hdop"], p["satellites"], p["battery_mv"], p["rssi"]
            ])


if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    specs = [
        ("normal", "templates/template_normal.csv", "G0703-00001"),
        ("cheat_highway", "templates/template_cheat.csv", "G0703-CHEATER"),
        ("suspicious_ab", "templates/template_suspicious.csv", "G0703-AB_COTE"),
        ("cheat_hsr", "templates/template_hsr.csv", "G0703-HSR"),
        ("gps_fault", "templates/template_gps_fault.csv", "G0703-FAULT"),
    ]
    for mode, filename, ring_id in specs:
        save_to_csv(filename, generate_csv_track(mode), ring_id)
        print(f"{filename}: {mode}")
