import math
from typing import List, Dict, Any, Tuple

EARTH_R = 6371000

HIGHWAY_CORRIDOR = [
    (25.1256, 121.7415), (25.0760, 121.5170), (25.0640, 121.3620),
    (24.9910, 121.3030), (24.9650, 121.2180), (24.8420, 121.0110),
    (24.7980, 121.0020), (24.6930, 120.9080),
]
HSR_CORRIDOR = [
    (25.0478, 121.5170), (25.0142, 121.4635), (25.0137, 121.2149),
    (24.8086, 121.0403), (24.6053, 120.8258),
]

RULES = {
    "GPS-R01": "超物理速度：連續區段速度超過賽鴿合理極限",
    "GPS-R02": "貼地高速：低高度卻呈現車輛速度",
    "GPS-R03": "高速公路重合：軌跡與國道/快速道路路廊高度接近",
    "GPS-R04": "高鐵路廊重合：高速段與台灣高鐵路廊接近",
    "GPS-R05": "非登記地點滯留：中途長時間低速停留",
    "GPS-R06": "GPS 品質異常：HDOP 高或衛星數過低，可能為設備/定位故障",
    "GPS-R07": "低頻不可判定：採樣間隔過大時，短段證據需保留不確定性",
}


def haversine_distance(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_segment_distance_m(p: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
    # 小範圍 equirectangular projection，足夠做沙盤路廊距離
    lat0 = math.radians((p[0] + a[0] + b[0]) / 3)
    def xy(q):
        return (math.radians(q[1]) * math.cos(lat0) * EARTH_R, math.radians(q[0]) * EARTH_R)
    px, py = xy(p); ax, ay = xy(a); bx, by = xy(b)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    projx, projy = ax + t * dx, ay + t * dy
    return math.hypot(px - projx, py - projy)


def distance_to_corridor_m(lat: float, lng: float, corridor: List[Tuple[float, float]]) -> float:
    return min(point_segment_distance_m((lat, lng), corridor[i], corridor[i + 1]) for i in range(len(corridor) - 1))


class PigeonFraudEngine:
    def __init__(self, normal_max_speed=95.0, highway_speed_threshold=72.0):
        self.normal_max_speed = normal_max_speed
        self.highway_speed_threshold = highway_speed_threshold

    def analyze_track(self, points: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(points) < 2:
            return {"status": "UNKNOWN", "risk_score": 0, "alerts": [], "details": "Points too few to analyze", "rules": RULES}

        alerts = []
        max_speed = 0.0
        critical = 0
        suspicious = 0
        gps_fault = 0
        stationary_run = 0
        segment_events = []

        for idx, p in enumerate(points):
            p["status"] = "PASS"
            p["anomaly_reason"] = ""
            p["rule_code"] = ""
            p["corridor"] = ""

        for idx in range(1, len(points)):
            prev = points[idx - 1]
            p = points[idx]
            dt = max(1, int(p.get("timestamp", 0)) - int(prev.get("timestamp", 0)))
            dist = haversine_distance(prev["lat"], prev["lng"], p["lat"], p["lng"])
            calc_speed = dist / dt * 3.6
            speed = max(float(p.get("speed_kmh") or 0), calc_speed)
            p["speed_kmh"] = round(speed, 1)
            max_speed = max(max_speed, speed)

            alt = float(p.get("alt") or 0)
            hdop = float(p.get("hdop") or 0)
            sats = int(p.get("satellites") or 0)
            prev_hdop = float(prev.get("hdop") or 0)
            prev_sats = int(prev.get("satellites") or 0)
            hwy_d = distance_to_corridor_m(p["lat"], p["lng"], HIGHWAY_CORRIDOR)
            hsr_d = distance_to_corridor_m(p["lat"], p["lng"], HSR_CORRIDOR)
            rules = []
            status = "PASS"
            reason_parts = []

            if hdop >= 3.5 or sats <= 5 or prev_hdop >= 3.5 or prev_sats <= 5:
                status = "GPS_FAULT"
                rules.append("GPS-R06")
                reason_parts.append(f"GPS 品質異常：HDOP {hdop} / 衛星 {sats}，需排除定位漂移與相鄰跳點")
                gps_fault += 1
            else:
                if speed > 150:
                    status = "CRITICAL_FRAUD"
                    rules.append("GPS-R01")
                    reason_parts.append(f"超物理速度 {speed:.1f} km/h")
                elif speed > self.normal_max_speed:
                    status = "SUSPICIOUS"
                    rules.append("GPS-R01")
                    reason_parts.append(f"速度偏高 {speed:.1f} km/h")

                if speed >= self.highway_speed_threshold and speed < 150 and alt < 90 and hwy_d < 900 and hsr_d >= 1200:
                    status = "CRITICAL_FRAUD"
                    rules.extend(["GPS-R02", "GPS-R03"])
                    reason_parts.append(f"貼地高速且距高速路廊約 {hwy_d:.0f}m")
                    p["corridor"] = "HIGHWAY"

                if speed >= 150 and alt < 90 and hsr_d < 1200:
                    status = "CRITICAL_FRAUD"
                    rules.extend(["GPS-R04"])
                    reason_parts.append(f"高速段距高鐵路廊約 {hsr_d:.0f}m，疑似台北→苗栗高鐵移動")
                    p["corridor"] = "HSR"

                if speed < 2.0:
                    stationary_run += 1
                    if stationary_run >= 5:
                        status = "SUSPICIOUS"
                        rules.append("GPS-R05")
                        reason_parts.append("連續低速滯留，疑似接應/AB 舍停留")
                else:
                    stationary_run = 0

            if status == "CRITICAL_FRAUD":
                critical += 1
            elif status == "SUSPICIOUS":
                suspicious += 1

            p["status"] = status
            p["rule_code"] = ",".join(dict.fromkeys(rules))
            p["anomaly_reason"] = "；".join(reason_parts)
            if rules:
                segment_events.append({
                    "seq": p.get("seq", idx + 1),
                    "level": "CRITICAL" if status == "CRITICAL_FRAUD" else "WARNING",
                    "rule_code": p["rule_code"],
                    "message": p["anomaly_reason"],
                })

        risk_score = min(100, critical * 5 + suspicious * 2 + max(0, gps_fault - 2) * 1)
        if critical >= 3:
            risk_score = max(risk_score, 88)
        elif suspicious >= 5:
            risk_score = max(risk_score, 45)
        elif gps_fault >= 2:
            risk_score = max(risk_score, 25)

        if any("GPS-R04" in e["rule_code"] for e in segment_events):
            alerts.append({"type": "GPS-R04 高鐵路廊重合", "level": "CRITICAL", "message": "偵測到台北/板橋至苗栗方向的高鐵速度與路廊重合片段。"})
        if any("GPS-R03" in e["rule_code"] for e in segment_events):
            alerts.append({"type": "GPS-R02/R03 貼地高速與國道路廊", "level": "CRITICAL", "message": "偵測到基隆接應後沿國道/快速道路南下至頭份附近的連續片段。"})
        if any("GPS-R05" in e["rule_code"] for e in segment_events):
            alerts.append({"type": "GPS-R05 中途滯留", "level": "WARNING", "message": "偵測到非歸巢點連續低速停留，需比對是否為 AB 舍或接應點。"})
        if gps_fault:
            alerts.append({"type": "GPS-R06 定位品質", "level": "WARNING", "message": f"偵測到 {gps_fault} 個 GPS 品質異常點；此類應標示設備/定位疑慮，不宜直接當作弊。"})
        if not alerts:
            alerts.append({"type": "PASS", "level": "INFO", "message": "未偵測到連續交通工具特徵或不合理滯留。"})

        status = "PASS"
        if risk_score >= 80:
            status = "CRITICAL_FRAUD"
        elif risk_score >= 35:
            status = "SUSPICIOUS"
        elif gps_fault:
            status = "GPS_FAULT_REVIEW"

        return {
            "status": status,
            "risk_score": risk_score,
            "highest_speed_kmh": round(max_speed, 1),
            "alerts": alerts,
            "segment_events": segment_events[:80],
            "rules": RULES,
            "critical_points_count": critical,
            "suspicious_points_count": suspicious,
            "gps_fault_points_count": gps_fault,
        }
