import math
from typing import List, Dict, Any

class PigeonFraudEngine:
    def __init__(self, normal_max_speed=85.0, highway_speed_threshold=90.0):
        """
        normal_max_speed: 鴿子理論最快飛行時速 (km/h) - 順風等極限通常不超過 85-90 km/h
        highway_speed_threshold: 高速公路貼合判定之時速下限 (km/h) - 高於此車速代表可能坐車
        """
        self.normal_max_speed = normal_max_speed
        self.highway_speed_threshold = highway_speed_threshold

    @staticmethod
    def haversine_distance(lat1, lon1, lat2, lon2):
        """計算兩點間的公尺距離"""
        R = 6371000  # 地球半徑 (公尺)
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(phi2 - phi1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = math.sin(delta_phi / 2) ** 2 + \
            math.cos(phi1) * math.cos(phi2) * \
            math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def analyze_track(self, points: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析整段 GPS 點，輸出作弊風險分數與具體違規事件。
        """
        if len(points) < 2:
            return {"risk_score": 0, "alerts": [], "details": "Points too few to analyze"}

        alerts = []
        highest_speed = 0.0
        overspeed_segments = 0
        total_points = len(points)
        
        # 1. 速度與停留異常檢測
        stationary_intervals = 0
        current_stationary_points = []
        
        for idx in range(1, len(points)):
            p_prev = points[idx-1]
            p_curr = points[idx]
            
            # 硬體回報的速度或手動計算的兩點速度
            speed = p_curr.get("speed_kmh", 0.0)
            highest_speed = max(highest_speed, speed)
            
            # A. 超速檢測
            if speed > self.normal_max_speed:
                overspeed_segments += 1
                
            # B. 滯留/停留點檢測 (時速小於2，極可能被關在籠子裡、AB舍等)
            if speed < 2.0:
                stationary_intervals += 1
                current_stationary_points.append(p_curr)

        # 2. 高速公路特徵檢測 (簡化演算法：如果超速，且平均高度和高度變化平緩)
        highway_match_points = []
        for p in points:
            # 模擬：如果速度 >= 90 且海拔高度平緩不變 (e.g. 50-80公尺)，標記為坐車可疑點
            if p.get("speed_kmh", 0.0) >= self.highway_speed_threshold and p.get("alt", 0.0) < 100.0:
                highway_match_points.append(p)

        # 3. 計算綜合風險分數
        risk_score = 0
        
        if overspeed_segments > 0:
            # 有超過鴿子極限速度的點
            speed_ratio = overspeed_segments / total_points
            if speed_ratio > 0.1: # 超過 10% 的點都大於 85km/h，極可能坐車
                risk_score += 60
                alerts.append({
                    "type": "OVER_SPEED",
                    "level": "CRITICAL",
                    "message": f"偵測到不合理超速點！最高時速達 {highest_speed:.1f} km/h，超速點比例 {speed_ratio*100:.1f}%。"
                })
            else:
                risk_score += 20
                alerts.append({
                    "type": "OVER_SPEED",
                    "level": "WARNING",
                    "message": f"偵測到少部分超速點。最高時速達 {highest_speed:.1f} km/h。"
                })

        if len(highway_match_points) > 10:
            risk_score += 30
            alerts.append({
                "type": "HIGHWAY_MATCH",
                "level": "CRITICAL",
                "message": f"軌跡特徵與陸路運輸高度重合！共偵測到 {len(highway_match_points)} 個貼合道路且定速運動的異常點。"
            })

        if stationary_intervals > 100: # 100點 = 1000秒 ≈ 16分鐘在某處滯留不動
            risk_score += 15
            alerts.append({
                "type": "AB_COTE_STATIONARY",
                "level": "WARNING",
                "message": f"賽鴿在中途疑似長點滯留 (可能在 AB 中繼舍停留)，滯留時間長達 {stationary_intervals*10/60:.1f} 分鐘。"
            })

        # 風險分數最大 100
        risk_score = min(risk_score, 100)
        
        # 決定最終狀態
        status = "PASS"
        if risk_score >= 60:
            status = "CRITICAL_FRAUD"
        elif risk_score >= 30:
            status = "SUSPICIOUS"

        return {
            "status": status,
            "risk_score": risk_score,
            "highest_speed_kmh": round(highest_speed, 1),
            "alerts": alerts,
            "overspeed_points_count": overspeed_segments,
            "stationary_minutes": round(stationary_intervals * 10 / 60, 1)
        }

if __name__ == "__main__":
    # 簡單的單元測試
    from simulator import generate_route
    
    engine = PigeonFraudEngine()
    
    print("--- 測試正常軌跡 ---")
    norm_pts = generate_route("normal")
    result_norm = engine.analyze_track(norm_pts)
    print(f"風險分數: {result_norm['risk_score']}, 狀態: {result_norm['status']}")
    for a in result_norm['alerts']:
        print(f"[{a['type']}] {a['message']}")
        
    print("\n--- 測試高速公路作弊軌跡 ---")
    cheat_pts = generate_route("cheat_highway")
    result_cheat = engine.analyze_track(cheat_pts)
    print(f"風險分數: {result_cheat['risk_score']}, 狀態: {result_cheat['status']}")
    for a in result_cheat['alerts']:
        print(f"[{a['type']}] {a['message']}")
