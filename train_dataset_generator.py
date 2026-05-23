import math
import random
import time
import os

# 放飛起點（以資格一海域放飛點為例，模擬在基隆外海）
START_LAT = 25.835123
START_LNG = 122.01416

# 預設歸巢終點：0703 大鴻鴿會（苗栗頭份田寮永貞宮）
COTE_LAT = 24.6853
COTE_LNG = 120.9023

# 台中/苗栗 某中繼舍 (AB 舍，模擬中途滯留)
AB_LAT = 24.8100
AB_LNG = 121.0500

# 國道一號真實高精度貼合路徑 (從台北重慶交流道 -> 桃園 -> 新竹 -> 苗栗頭份交流道)
HIGHWAY_PATH = [
    (25.1280, 121.7390), # 基隆端上交流道
    (25.0760, 121.5170), # 台北重慶交流道
    (25.0640, 121.3620), # 林口交流道
    (24.9910, 121.3030), # 桃園交流道
    (24.9650, 121.2180), # 中壢交流道
    (24.8420, 121.0110), # 竹北交流道
    (24.7980, 121.0020), # 新竹交流道
    (24.6930, 120.9080)  # 頭份交流道 (下交流道放飛點)
]

# 台灣高鐵高精度貼合路徑 (南港 -> 台北 -> 板橋 -> 桃園 -> 新竹 -> 苗栗高鐵站)
HSR_PATH = [
    (25.0521, 121.6068), # 南港
    (25.0461, 121.5175), # 台北
    (25.0132, 121.4624), # 板橋
    (25.0130, 121.2147), # 桃園高鐵
    (24.8084, 121.0402), # 新竹高鐵
    (24.6015, 120.8252)  # 苗栗高鐵站 (下車放飛點)
]

def generate_csv_track(mode="normal"):
    """
    生成高度逼真、符合行為科學的 CSV 測試軌跡
    - normal: 正常信鴿飛行 (65-80 km/h), 高度 150-350m, 海域直接歸巢。
    - cheat_highway: 正常飛到基隆登陸點 -> 下車走大武崙/基隆交流道上國道一號 (定速 95-110 km/h, 高度 50m) -> 苗栗頭份交流道下車 -> 放飛繼續正常飛回大鴻鴿舍。
    - suspicious_ab: 正常飛行 -> 誤入中繼舍 (AB 舍) 滯留 35 分鐘 -> 驚醒放飛繼續正常飛回大鴻鴿舍。
    - cheat_hsr: 正常飛到南港/台北 -> 台北/板橋高鐵站上高鐵 (極速 250-295 km/h, 高度 50-60m) -> 苗栗高鐵站下車 -> 放飛繼續正常飛回大鴻鴿舍。
    """
    points = []
    base_time = int(time.time()) - 8 * 3600  # 8小時前開始
    seq = 1

    if mode == "normal":
        # 100% 正常飛行：基隆外海放飛，朝著頭份大鴻鴿舍飛行
        steps = 100
        for i in range(steps + 1):
            ratio = i / steps
            noise_lat = random.uniform(-0.0015, 0.0015) if i < steps else 0
            noise_lng = random.uniform(-0.0015, 0.0015) if i < steps else 0
            lat = START_LAT + (COTE_LAT - START_LAT) * ratio + noise_lat
            lng = START_LNG + (COTE_LNG - START_LNG) * ratio + noise_lng
            
            # 高度動態變化：起飛 -> 升空巡航 (200-300m) -> 降落
            if i < 5:
                alt = 15.0 + (i * 40.0) # 漸漸起飛
            elif i > steps - 5:
                alt = 20.0 + ((steps - i) * 35.0) # 漸漸降落歸巢
            else:
                alt = random.uniform(180, 290)
                
            speed = random.uniform(65, 80) if i < steps else 0.0
            
            points.append({
                "seq": seq, "timestamp": base_time + (i * 60),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(alt, 1),
                "speed_kmh": round(speed, 1), "heading": round(random.uniform(170, 190), 1),
                "hdop": 0.9, "satellites": 12, "battery_mv": 3950 - (i * 2), "rssi": -85
            })
            seq += 1

    elif mode == "cheat_highway":
        # 1. 正常海域飛行段：放飛點 -> 基隆端上交流道點 (HIGHWAY_PATH[0])
        steps_fly = 25
        for i in range(steps_fly):
            ratio = i / steps_fly
            lat = START_LAT + (HIGHWAY_PATH[0][0] - START_LAT) * ratio + random.uniform(-0.001, 0.001)
            lng = START_LNG + (HIGHWAY_PATH[0][1] - START_LNG) * ratio + random.uniform(-0.001, 0.001)
            alt = random.uniform(150, 250) if i > 3 else 15.0 + (i * 30.0)
            points.append({
                "seq": seq, "timestamp": base_time + (seq * 60),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(alt, 1),
                "speed_kmh": round(random.uniform(65, 78), 1), "heading": 180.0,
                "hdop": 0.9, "satellites": 11, "battery_mv": 3950, "rssi": -80
            })
            seq += 1
            
        # 2. 國道作弊段：沿高速公路南下到頭份交流道
        for i in range(len(HIGHWAY_PATH) - 1):
            p1 = HIGHWAY_PATH[i]
            p2 = HIGHWAY_PATH[i+1]
            sub_steps = 10
            for j in range(sub_steps):
                ratio = j / sub_steps
                lat = p1[0] + (p2[0] - p1[0]) * ratio
                lng = p1[1] + (p2[1] - p1[1]) * ratio
                
                # 坐車高度平緩貼地（海拔 50m~65m 抖動），時速 95-110 km/h
                speed = random.uniform(96.0, 112.0)
                points.append({
                    "seq": seq, "timestamp": base_time + (seq * 60),
                    "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(random.uniform(45, 55), 1),
                    "speed_kmh": round(speed, 1), "heading": 185.0,
                    "hdop": 0.8, "satellites": 14, "battery_mv": 3910, "rssi": -90
                })
                seq += 1

        # 3. 交流道下車後放飛段：從頭份交流道 (HIGHWAY_PATH[-1]) 正常飛回 苗栗大鴻鴿舍
        steps_release = 10
        p_release = HIGHWAY_PATH[-1]
        for i in range(steps_release + 1):
            ratio = i / steps_release
            lat = p_release[0] + (COTE_LAT - p_release[0]) * ratio + random.uniform(-0.0005, 0.0005) if i < steps_release else COTE_LAT
            lng = p_release[1] + (COTE_LNG - p_release[1]) * ratio + random.uniform(-0.0005, 0.0005) if i < steps_release else COTE_LNG
            
            # 放飛高度爬升至 120m-160m，再降落至大鴻鴿會的 20m
            if i == steps_release:
                alt = 20.0
                speed = 0.0
            else:
                alt = 60.0 + (i * 12.0) if i < 5 else 140.0 - ((i-5) * 15.0)
                speed = random.uniform(55, 68)
                
            points.append({
                "seq": seq, "timestamp": base_time + (seq * 60),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(alt, 1),
                "speed_kmh": round(speed, 1), "heading": 180.0,
                "hdop": 0.9, "satellites": 12, "battery_mv": 3870, "rssi": -85
            })
            seq += 1

    elif mode == "suspicious_ab":
        # 1. 正常海域飛行段：放飛點 -> 中繼舍 (AB_LAT, AB_LNG)
        steps_fly = 50
        for i in range(steps_fly):
            ratio = i / steps_fly
            lat = START_LAT + (AB_LAT - START_LAT) * ratio + random.uniform(-0.001, 0.001)
            lng = START_LNG + (AB_LNG - START_LNG) * ratio + random.uniform(-0.001, 0.001)
            points.append({
                "seq": seq, "timestamp": base_time + (seq * 60),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(random.uniform(150, 300), 1),
                "speed_kmh": round(random.uniform(65, 78), 1), "heading": 180.0,
                "hdop": 0.9, "satellites": 12, "battery_mv": 3950, "rssi": -85
            })
            seq += 1
            
        # 2. 在中繼舍(網鴿點/AB 舍) 滯留 35 分鐘 (每 60 秒一點)
        for i in range(35):
            points.append({
                "seq": seq, "timestamp": base_time + (seq * 60),
                "lat": round(AB_LAT + random.uniform(-0.0001, 0.0001), 6),
                "lng": round(AB_LNG + random.uniform(-0.0001, 0.0001), 6),
                "alt": 48.0,
                "speed_kmh": round(random.uniform(0.0, 1.2), 1), "heading": 0.0,
                "hdop": 1.2, "satellites": 9, "battery_mv": 3910, "rssi": -65
            })
            seq += 1
            
        # 3. 繼續飛回大鴻鴿舍
        steps_end = 35
        for i in range(steps_end + 1):
            ratio = i / steps_end
            lat = AB_LAT + (COTE_LAT - AB_LAT) * ratio + random.uniform(-0.001, 0.001) if i < steps_end else COTE_LAT
            lng = AB_LNG + (COTE_LNG - AB_LNG) * ratio + random.uniform(-0.001, 0.001) if i < steps_end else COTE_LNG
            alt = random.uniform(150, 260) if i < steps_end else 20.0
            points.append({
                "seq": seq, "timestamp": base_time + (seq * 60),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(alt, 1),
                "speed_kmh": round(random.uniform(60, 75), 1) if i < steps_end else 0.0, "heading": 180.0,
                "hdop": 0.9, "satellites": 12, "battery_mv": 3850, "rssi": -85
            })
            seq += 1

    elif mode == "cheat_hsr":
        # 1. 正常海域飛行段：放飛點 -> 台北/板橋高鐵站附近 (HSR_PATH[2])
        steps_fly = 35
        for i in range(steps_fly):
            ratio = i / steps_fly
            lat = START_LAT + (HSR_PATH[2][0] - START_LAT) * ratio + random.uniform(-0.001, 0.001)
            lng = START_LNG + (HSR_PATH[2][1] - START_LNG) * ratio + random.uniform(-0.001, 0.001)
            points.append({
                "seq": seq, "timestamp": base_time + (seq * 60),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(random.uniform(150, 320), 1),
                "speed_kmh": round(random.uniform(65, 78), 1), "heading": 180.0,
                "hdop": 0.9, "satellites": 12, "battery_mv": 3950, "rssi": -80
            })
            seq += 1
            
        # 2. 坐高鐵高速移動 (板橋 -> 桃園 -> 新竹 -> 苗栗)
        for i in range(2, len(HSR_PATH) - 1):
            p1 = HSR_PATH[i]
            p2 = HSR_PATH[i+1]
            sub_steps = 10
            for j in range(sub_steps):
                ratio = j / sub_steps
                lat = p1[0] + (p2[0] - p1[0]) * ratio
                lng = p1[1] + (p2[1] - p1[1]) * ratio
                # 高鐵車速 240-295 km/h，高度在 50m~60m 貼地
                speed = random.uniform(240.0, 295.0)
                points.append({
                    "seq": seq, "timestamp": base_time + (seq * 60),
                    "lat": round(lat, 6), "lng": round(lng, 6), "alt": 55.0,
                    "speed_kmh": round(speed, 1), "heading": 180.0,
                    "hdop": 0.7, "satellites": 15, "battery_mv": 3900, "rssi": -95
                })
                seq += 1

        # 3. 苗栗高鐵站下車後放飛段：從苗栗高鐵站 (HSR_PATH[-1]) 正常飛回 苗栗大鴻鴿舍
        steps_release = 15
        p_release = HSR_PATH[-1]
        for i in range(steps_release + 1):
            ratio = i / steps_release
            lat = p_release[0] + (COTE_LAT - p_release[0]) * ratio + random.uniform(-0.001, 0.001) if i < steps_release else COTE_LAT
            lng = p_release[1] + (COTE_LNG - p_release[1]) * ratio + random.uniform(-0.001, 0.001) if i < steps_release else COTE_LNG
            
            if i == steps_release:
                alt = 20.0
                speed = 0.0
            else:
                alt = 55.0 + (i * 15.0) if i < 6 else 145.0 - ((i-6) * 10.0)
                speed = random.uniform(55, 68)
                
            points.append({
                "seq": seq, "timestamp": base_time + (seq * 60),
                "lat": round(lat, 6), "lng": round(lng, 6), "alt": round(alt, 1),
                "speed_kmh": round(speed, 1), "heading": 180.0,
                "hdop": 0.9, "satellites": 12, "battery_mv": 3850, "rssi": -85
            })
            seq += 1

    return points

def save_to_csv(filename, points, ring_id="G0703-00001"):
    import csv
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "seq", "timestamp", "lat", "lng", "alt", "speed_kmh", "heading", "hdop", "satellites", "battery_mv", "rssi"])
        for p in points:
            writer.writerow([
                ring_id,
                p["seq"], p["timestamp"], p["lat"], p["lng"], p["alt"],
                p["speed_kmh"], p["heading"], p["hdop"], p["satellites"],
                p["battery_mv"], p["rssi"]
            ])

if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    
    # 正常
    normal_pts = generate_csv_track("normal")
    save_to_csv("templates/template_normal.csv", normal_pts, "G0703-00001")
    
    # 國道作弊
    cheat_pts = generate_csv_track("cheat_highway")
    save_to_csv("templates/template_cheat.csv", cheat_pts, "G0703-CHEATER")
    
    # 滯留
    stay_pts = generate_csv_track("suspicious_ab")
    save_to_csv("templates/template_suspicious.csv", stay_pts, "G0703-AB_COTE")
    
    # 高鐵作弊
    hsr_pts = generate_csv_track("cheat_hsr")
    save_to_csv("templates/template_hsr.csv", hsr_pts, "G0703-HSR")
    
    print("CSV Templates generated successfully in templates/")
