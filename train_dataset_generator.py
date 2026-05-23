import math
import random
import time
import os
from datetime import datetime

# 放鴿點（基隆港外海）
START_LAT = 25.1500
START_LNG = 121.7500

# 預設：0703 大鴻鴿會（苗栗頭份田寮永貞宮）
COTE_LAT = 24.6853
COTE_LNG = 120.9023

# 第二組備用：0264 上兆鴿會（桃園市蘆竹區大竹大興路260-7號）
COTE_桃園_LAT = 25.0195
COTE_桃園_LNG = 121.2588

# 苗栗頭份 AB 舍 (中繼點，模擬中途滯留)
AB_LAT = 24.8100
AB_LNG = 121.0500

# 國道一號路徑 (基隆外海放飛 -> 台北 -> 桃園 -> 苗栗頭份 0703 大鴻鴿會)
HIGHWAY_PATH = [
    (25.0600, 121.5200), # 台北
    (25.0100, 121.3000), # 桃園
    (24.8500, 121.0000), # 新竹
    (24.6853, 120.9023)  # 苗栗頭份大鴻
]

def generate_csv_track(mode="normal"):
    """
    生成高度逼真的 CSV 測試軌跡
    - normal: 正常信鴿飛行 (65-80 km/h), 高度 150-350m
    - cheat_highway: 正常飛到台中後，坐車走國道一號 (95-115 km/h), 高度 70m
    - suspicious_ab: 正常飛行，但在台中停留 25 分鐘 (0-1.5 km/h), 高度 50m
    - cheat_hsr: 沿高鐵坐車/高鐵移動 (120-250 km/h)
    """
    points = []
    base_time = int(time.time()) - 8 * 3600 # 8小時前開始
    seq = 1

    if mode == "normal":
        steps = 100
        for i in range(steps + 1):
            ratio = i / steps
            noise_lat = random.uniform(-0.003, 0.003)
            noise_lng = random.uniform(-0.003, 0.003)
            lat = START_LAT + (COTE_LAT - START_LAT) * ratio + noise_lat
            lng = START_LNG + (COTE_LNG - START_LNG) * ratio + noise_lng
            alt = random.uniform(150, 350)
            speed = random.uniform(65, 80)
            
            points.append({
                "seq": seq, "timestamp": base_time + (i * 180),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(alt, 1),
                "speed_kmh": round(speed, 1), "heading": round(random.uniform(170, 190), 1),
                "hdop": 0.9, "satellites": 12, "battery_mv": 3950, "rssi": -85
            })
            seq += 1

    elif mode == "cheat_highway":
        # 飛到台中 AB 舍
        steps = 50
        for i in range(steps):
            ratio = i / steps
            lat = START_LAT + (AB_LAT - START_LAT) * ratio + random.uniform(-0.002, 0.002)
            lng = START_LNG + (AB_LNG - START_LNG) * ratio + random.uniform(-0.002, 0.002)
            points.append({
                "seq": seq, "timestamp": base_time + (i * 180),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(random.uniform(150, 300), 1),
                "speed_kmh": round(random.uniform(60, 75), 1), "heading": 180.0,
                "hdop": 0.9, "satellites": 12, "battery_mv": 3950, "rssi": -85
            })
            seq += 1
            
        # 坐車移動 國道一號
        hw_start_time = base_time + (steps * 180)
        hw_steps = len(HIGHWAY_PATH)
        for i in range(hw_steps - 1):
            p1 = HIGHWAY_PATH[i]
            p2 = HIGHWAY_PATH[i+1]
            sub_steps = 10
            for j in range(sub_steps):
                ratio = j / sub_steps
                lat = p1[0] + (p2[0] - p1[0]) * ratio
                lng = p1[1] + (p2[1] - p1[1]) * ratio
                points.append({
                    "seq": seq, "timestamp": hw_start_time + ((i * sub_steps + j) * 180),
                    "lat": round(lat, 6), "lng": round(lng, 6), "alt": 70.0,
                    "speed_kmh": round(random.uniform(95.0, 115.0), 1), "heading": 180.0,
                    "hdop": 0.8, "satellites": 14, "battery_mv": 3900, "rssi": -90
                })
                seq += 1

    elif mode == "suspicious_ab":
        # 飛到台中 AB 舍
        steps = 50
        for i in range(steps):
            ratio = i / steps
            lat = START_LAT + (AB_LAT - START_LAT) * ratio + random.uniform(-0.002, 0.002)
            lng = START_LNG + (AB_LNG - START_LNG) * ratio + random.uniform(-0.002, 0.002)
            points.append({
                "seq": seq, "timestamp": base_time + (i * 180),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(random.uniform(150, 300), 1),
                "speed_kmh": round(random.uniform(60, 75), 1), "heading": 180.0,
                "hdop": 0.9, "satellites": 12, "battery_mv": 3950, "rssi": -85
            })
            seq += 1
            
        # 在 AB 舍停留 25 分鐘 (每 180s 一點, 約 8 點)
        stay_start_time = base_time + (steps * 180)
        for i in range(8):
            points.append({
                "seq": seq, "timestamp": stay_start_time + (i * 180),
                "lat": round(AB_LAT + random.uniform(-0.0001, 0.0001), 6),
                "lng": round(AB_LNG + random.uniform(-0.0001, 0.0001), 6),
                "alt": 50.0,
                "speed_kmh": round(random.uniform(0.0, 1.5), 1), "heading": 0.0,
                "hdop": 1.1, "satellites": 10, "battery_mv": 3920, "rssi": -70
            })
            seq += 1
            
        # 繼續飛回嘉義
        resume_start_time = stay_start_time + (8 * 180)
        steps_end = 50
        for i in range(steps_end + 1):
            ratio = i / steps_end
            lat = AB_LAT + (COTE_LAT - AB_LAT) * ratio + random.uniform(-0.002, 0.002)
            lng = AB_LNG + (COTE_LNG - AB_LNG) * ratio + random.uniform(-0.002, 0.002)
            points.append({
                "seq": seq, "timestamp": resume_start_time + (i * 180),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(random.uniform(150, 300), 1),
                "speed_kmh": round(random.uniform(60, 75), 1), "heading": 180.0,
                "hdop": 0.9, "satellites": 12, "battery_mv": 3900, "rssi": -85
            })
            seq += 1

    elif mode == "cheat_hsr":
        steps = 60
        for i in range(steps + 1):
            ratio = i / steps
            lat = START_LAT + (COTE_LAT - START_LAT) * ratio
            lng = START_LNG + (COTE_LNG - START_LNG) * ratio
            # 高鐵行車速度
            speed = random.uniform(150.0, 245.0)
            points.append({
                "seq": seq, "timestamp": base_time + (i * 180),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": 65.0,
                "speed_kmh": round(speed, 1), "heading": 180.0,
                "hdop": 0.8, "satellites": 14, "battery_mv": 3950, "rssi": -95
            })
            seq += 1

    return points

def save_to_csv(filename, points):
    import csv
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["seq", "timestamp", "lat", "lng", "alt", "speed_kmh", "heading", "hdop", "satellites", "battery_mv", "rssi"])
        for p in points:
            writer.writerow([
                p["seq"], p["timestamp"], p["lat"], p["lng"], p["alt"],
                p["speed_kmh"], p["heading"], p["hdop"], p["satellites"],
                p["battery_mv"], p["rssi"]
            ])

if __name__ == "__main__":
    # 在當前目錄直接輸出，並確保 demo 網頁可以直接載入
    os.makedirs("templates", exist_ok=True)
    
    # 正常
    normal_pts = generate_csv_track("normal")
    save_to_csv("templates/template_normal.csv", normal_pts)
    
    # 作弊
    cheat_pts = generate_csv_track("cheat_highway")
    save_to_csv("templates/template_cheat.csv", cheat_pts)
    
    # 滯留
    susp_pts = generate_csv_track("suspicious_ab")
    save_to_csv("templates/template_suspicious.csv", susp_pts)
    
    print("CSV Templates generated successfully in templates/")
