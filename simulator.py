import math
import time
import requests
import random
from datetime import datetime

# 台灣部分標誌性的經緯度座標 (嘉義地區與週邊作弊場景)
# 放鴿點 (例如：基隆港外海或北部某海岸)
START_LAT = 25.1500
START_LNG = 121.7500

# 正常鴿舍 (嘉義 8888)
COTE_LAT = 23.4800
COTE_LNG = 120.4500

# 作弊 AB 舍 (例如在台中 7777 先截留，再走高速公路或同車運往嘉義)
AB_COTE_LAT = 24.1500
AB_COTE_LNG = 120.6500

# 國道一號 (台中到嘉義段) 經緯度取樣點模擬高速公路移動
HIGHWAY_PATH = [
    (24.1500, 120.6500), # 台中 AB 舍
    (24.0500, 120.5300), # 彰化
    (23.8500, 120.4500), # 溪州
    (23.7000, 120.4300), # 西螺
    (23.6000, 120.4300), # 斗南
    (23.4800, 120.4500)  # 嘉義
]

def generate_route(mode="normal"):
    """
    產生模擬 GPS 軌跡
    - mode="normal": 正常飛行 (平均時速 60-80 km/h) 直線加小氣流擾動朝向主鴿舍
    - mode="cheat_highway": 正常飛到台中 AB 舍 (2.5小時)，接著突然速度暴增貼合高速公路移動到嘉義主鴿舍 (坐車移動)
    """
    points = []
    current_time = int(time.time()) - 8 * 3600 # 8小時前開始
    seq = 1
    
    if mode == "normal":
        # 正常航線：START -> COTE (直線距離約 220 km，賽鴿時速 70km/h 約需 3-4 小時)
        total_steps = 1200 # 每10秒一點，1200點約3.3小時
        for i in range(total_steps):
            ratio = i / total_steps
            # 引入風向小隨機偏差
            noise_lat = random.uniform(-0.005, 0.005)
            noise_lng = random.uniform(-0.005, 0.005)
            
            lat = START_LAT + (COTE_LAT - START_LAT) * ratio + noise_lat
            lng = START_LNG + (COTE_LNG - START_LNG) * ratio + noise_lng
            
            # 隨機高度 (200m - 500m 飛行)
            alt = random.uniform(150, 450)
            speed = random.uniform(65, 85) # 鴿子合理時速
            
            points.append({
                "seq": seq,
                "timestamp": current_time + (i * 10),
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "alt": round(alt, 1),
                "speed_kmh": round(speed, 1),
                "heading": round(random.uniform(170, 195), 1),
                "hdop": 0.9,
                "satellites": 12,
                "battery_mv": max(3600, 4200 - int(i * 0.4)), # 慢慢掉電
                "rssi": -85
            })
            seq += 1
            
    elif mode == "cheat_highway":
        # 正常飛到 AB 舍
        steps_to_ab = 600
        for i in range(steps_to_ab):
            ratio = i / steps_to_ab
            noise_lat = random.uniform(-0.002, 0.002)
            noise_lng = random.uniform(-0.002, 0.002)
            lat = START_LAT + (AB_COTE_LAT - START_LAT) * ratio + noise_lat
            lng = START_LNG + (AB_COTE_LNG - START_LNG) * ratio + noise_lng
            
            points.append({
                "seq": seq,
                "timestamp": current_time + (i * 10),
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "alt": round(random.uniform(150, 400), 1),
                "speed_kmh": round(random.uniform(60, 75), 1),
                "heading": round(random.uniform(170, 190), 1),
                "hdop": 0.9,
                "satellites": 12,
                "battery_mv": 4200 - int(i * 0.4),
                "rssi": -85
            })
            seq += 1
            
        # 在 AB 舍停留 30 分鐘 (GPS 點幾乎重疊，高度低，時速降到 0-2)
        ab_time_start = current_time + (steps_to_ab * 10)
        for i in range(180): # 180 * 10s = 30 min
            points.append({
                "seq": seq,
                "timestamp": ab_time_start + (i * 10),
                "lat": round(AB_COTE_LAT + random.uniform(-0.0001, 0.0001), 6),
                "lng": round(AB_COTE_LNG + random.uniform(-0.0001, 0.0001), 6),
                "alt": 50.0, # 地面高度
                "speed_kmh": round(random.uniform(0.0, 1.5), 1),
                "heading": 0.0,
                "hdop": 1.2,
                "satellites": 8,
                "battery_mv": 4200 - int((steps_to_ab + i) * 0.4),
                "rssi": -70
            })
            seq += 1
            
        # 坐上貨車走高速公路! (速度 95-110 km/h, 貼合 HIGHWAY_PATH 點)
        hw_time_start = ab_time_start + (180 * 10)
        hw_steps = len(HIGHWAY_PATH)
        for i in range(hw_steps - 1):
            p1 = HIGHWAY_PATH[i]
            p2 = HIGHWAY_PATH[i+1]
            # 兩點之間細分 100 步
            sub_steps = 80
            for j in range(sub_steps):
                ratio = j / sub_steps
                lat = p1[0] + (p2[0] - p1[0]) * ratio
                lng = p1[1] + (p2[1] - p1[1]) * ratio
                
                step_index = i * sub_steps + j
                points.append({
                    "seq": seq,
                    "timestamp": hw_time_start + (step_index * 10),
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "alt": 70.0, # 高速公路海拔
                    "speed_kmh": round(random.uniform(95.0, 115.0), 1), # 汽車速度
                    "heading": round(random.uniform(175, 185), 1),
                    "hdop": 0.8,
                    "satellites": 14,
                    "battery_mv": 4200 - int((steps_to_ab + 180 + step_index) * 0.4),
                    "rssi": -90
                })
                seq += 1
                
    return points

def simulate_device_upload(device="G0703-00001", mode="normal", url_base="http://127.0.0.1:8801"):
    """模擬發送設定 API 與批次軌跡 API"""
    config_url = f"{url_base}/api/v1/devices/config"
    ingest_url = f"{url_base}/api/v1/tracks/ingest"
    
    # 1. 模擬發送裝置設定
    config_payload = {
        "device": device,
        "race": "115D1X",
        "cote": "8888",
        "ring": "660001" if mode == "normal" else "660002",
        "log_interval_sec": 10,
        "upload_interval_sec": 60,
        "mode": "hybrid",
        "gps_power_mode": "normal",
        "start_time": datetime.now().isoformat()
    }
    
    print(f"\n--- [模擬] 1. 發送裝置設定給 {config_url} ---")
    try:
        r = requests.post(config_url, json=config_payload, timeout=5)
        print("Response:", r.json())
    except Exception as e:
        print("Error sending config:", e)
        return
        
    # 2. 模擬生成軌跡數據
    print(f"--- [模擬] 2. 產生軌跡數據 (模式: {mode}) ---")
    points = generate_route(mode=mode)
    print(f"共生成 {len(points)} 個軌跡點。")
    
    # 3. 模擬批次上傳 (每60個點打包上報，模擬真實 hybrid upload)
    print("--- [模擬] 3. 批次上報軌跡數據 (每 60 點上報一次) ---")
    batch_size = 60
    for idx in range(0, len(points), batch_size):
        batch_points = points[idx:idx+batch_size]
        ingest_payload = {
            "device": device,
            "race": "115D1X",
            "cote": "8888",
            "ring": config_payload["ring"],
            "points": batch_points
        }
        try:
            r = requests.post(ingest_url, json=ingest_payload, timeout=5)
            print(f"上報批次 {idx//batch_size + 1}/{math.ceil(len(points)/batch_size)}: 成功寫入 {r.json().get('inserted', len(batch_points))} 點")
        except Exception as e:
            print(f"Error ingesting batch: {e}")
            break

if __name__ == "__main__":
    # 執行本地端測試：先模擬正常鴿子，再模擬走高速公路作弊鴿子
    simulate_device_upload(device="G0703-00001", mode="normal")
    simulate_device_upload(device="G0703-00002", mode="cheat_highway")
