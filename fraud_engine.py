import math
from typing import List, Dict, Any, Tuple, Optional

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

DEFAULT_RULE_PROFILE = {
    "min_corridor_match_km": 20,
    "highway_corridor_distance_m": 900,
    "hsr_corridor_distance_m": 1200,
    "ground_altitude_max_m": 90,
    "highway_speed_min_kmh": 72,
    "hsr_speed_min_kmh": 150,
    "normal_max_speed_kmh": 95,
    "gps_hdop_max": 3.5,
    "gps_satellites_min": 5,
    "stationary_speed_max_kmh": 2.0,
    "stationary_points_min": 5,
}

RULES = {
    "GPS-R01": "超物理速度：連續區段速度超過賽鴿合理極限",
    "GPS-R02": "貼地高速：低高度卻呈現車輛速度",
    "GPS-R03-PARTIAL": "高速/快速道路短段貼近：未達連續路廊門檻，需人工複核",
    "GPS-R03-CORRIDOR-RUN": "高速/快速道路連續路廊證據：連續距離達門檻才判高危",
    "GPS-R04-PARTIAL": "高鐵短段貼近：未達連續路廊門檻，需人工複核",
    "GPS-R04-CORRIDOR-RUN": "高鐵連續路廊證據：連續距離達門檻才判高危",
    "GPS-R05": "非登記地點滯留：中途長時間低速停留",
    "GPS-R06": "GPS 品質異常：HDOP 高或衛星數過低，可能為設備/定位故障",
    "GPS-R07": "低頻不可判定：採樣間隔過大時，短段證據需保留不確定性",
    "GPS-R08": "疑似擄鴿/攔鳥網起點：正常飛行後山區/非登記點異常滯留再轉運",
    "GPS-R09": "山區異常滯留：非歸返點長時間低速停留",
    "GPS-R10": "轉運起點：滯留後接續車輛/高鐵連續路廊證據",
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

    px, py = xy(p)
    ax, ay = xy(a)
    bx, by = xy(b)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    projx, projy = ax + t * dx, ay + t * dy
    return math.hypot(px - projx, py - projy)


def distance_to_corridor_m(lat: float, lng: float, corridor: List[Tuple[float, float]]) -> float:
    return min(point_segment_distance_m((lat, lng), corridor[i], corridor[i + 1]) for i in range(len(corridor) - 1))


def _merge_profile(rule_profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    profile = dict(DEFAULT_RULE_PROFILE)
    if rule_profile:
        for key, value in rule_profile.items():
            if key in profile and value is not None:
                profile[key] = value
    return profile


class PigeonFraudEngine:
    def __init__(self, normal_max_speed=95.0, highway_speed_threshold=72.0, rule_profile: Optional[Dict[str, Any]] = None):
        self.rule_profile = _merge_profile(rule_profile)
        # 保留舊參數相容，但以 rule_profile 為主。
        if rule_profile is None:
            self.rule_profile["normal_max_speed_kmh"] = normal_max_speed
            self.rule_profile["highway_speed_min_kmh"] = highway_speed_threshold
        self.normal_max_speed = float(self.rule_profile["normal_max_speed_kmh"])
        self.highway_speed_threshold = float(self.rule_profile["highway_speed_min_kmh"])

    def _build_run(self, points: List[Dict[str, Any]], segments: List[Dict[str, Any]], corridor_type: str, start_i: int, end_i: int) -> Dict[str, Any]:
        selected = segments[start_i:end_i + 1]
        start_pt = points[selected[0]["start_idx"]]
        end_pt = points[selected[-1]["end_idx"]]
        dist_km = sum(s["distance_m"] for s in selected) / 1000
        speeds = [s["speed_kmh"] for s in selected]
        alts = [float(points[s["end_idx"]].get("alt") or 0) for s in selected]
        corridor_distances = [s["corridor_distance_m"] for s in selected if s.get("corridor_type") == corridor_type]
        rule = "GPS-R03-CORRIDOR-RUN" if corridor_type == "highway" else "GPS-R04-CORRIDOR-RUN"
        return {
            "start_seq": start_pt.get("seq", selected[0]["start_idx"] + 1),
            "end_seq": end_pt.get("seq", selected[-1]["end_idx"] + 1),
            "start_time": start_pt.get("timestamp"),
            "end_time": end_pt.get("timestamp"),
            "distance_km": round(dist_km, 2),
            "avg_speed_kmh": round(sum(speeds) / max(1, len(speeds)), 1),
            "max_speed_kmh": round(max(speeds), 1),
            "min_alt_m": round(min(alts), 1) if alts else 0,
            "max_alt_m": round(max(alts), 1) if alts else 0,
            "avg_corridor_distance_m": round(sum(corridor_distances) / max(1, len(corridor_distances)), 1),
            "corridor_type": corridor_type,
            "status": "CRITICAL_FRAUD",
            "rule_codes": ["GPS-R02", rule] if corridor_type == "highway" else [rule],
            "reason": f"連續 {dist_km:.1f}km 貼近{'高速/快速道路' if corridor_type == 'highway' else '高鐵'}路廊，達到 {self.rule_profile['min_corridor_match_km']}km 門檻",
        }

    def _corridor_runs(self, points: List[Dict[str, Any]], segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        runs: List[Dict[str, Any]] = []
        min_km = float(self.rule_profile["min_corridor_match_km"])
        idx = 0
        while idx < len(segments):
            ctype = segments[idx].get("corridor_type")
            if not ctype:
                idx += 1
                continue
            start = idx
            dist_m = 0.0
            while idx < len(segments) and segments[idx].get("corridor_type") == ctype:
                dist_m += segments[idx]["distance_m"]
                idx += 1
            end = idx - 1
            if dist_m / 1000 >= min_km:
                runs.append(self._build_run(points, segments, ctype, start, end))
        return runs

    def analyze_track(self, points: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(points) < 2:
            return {"status": "UNKNOWN", "risk_score": 0, "alerts": [], "details": "Points too few to analyze", "rules": RULES, "rule_profile": self.rule_profile}

        alerts = []
        max_speed = 0.0
        critical = 0
        suspicious = 0
        gps_fault = 0
        stationary_run = 0
        segment_events = []
        segments: List[Dict[str, Any]] = []

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
            corridor_type = ""
            corridor_distance = None

            if hdop >= self.rule_profile["gps_hdop_max"] or sats <= self.rule_profile["gps_satellites_min"] or prev_hdop >= self.rule_profile["gps_hdop_max"] or prev_sats <= self.rule_profile["gps_satellites_min"]:
                status = "GPS_FAULT"
                rules.append("GPS-R06")
                reason_parts.append(f"GPS 品質異常：HDOP {hdop} / 衛星 {sats}，需排除定位漂移與相鄰跳點")
                gps_fault += 1
            else:
                if speed > self.rule_profile["hsr_speed_min_kmh"] and hsr_d >= self.rule_profile["hsr_corridor_distance_m"]:
                    status = "CRITICAL_FRAUD"
                    rules.append("GPS-R01")
                    reason_parts.append(f"超物理速度 {speed:.1f} km/h")
                elif speed > self.normal_max_speed:
                    status = "SUSPICIOUS"
                    rules.append("GPS-R01")
                    reason_parts.append(f"速度偏高 {speed:.1f} km/h")

                if speed >= self.rule_profile["highway_speed_min_kmh"] and speed < self.rule_profile["hsr_speed_min_kmh"] and alt < self.rule_profile["ground_altitude_max_m"] and hwy_d < self.rule_profile["highway_corridor_distance_m"] and hsr_d >= self.rule_profile["hsr_corridor_distance_m"]:
                    status = "SUSPICIOUS"
                    rules.extend(["GPS-R02", "GPS-R03-PARTIAL"])
                    reason_parts.append(f"短段貼地高速且距高速路廊約 {hwy_d:.0f}m；需累積達 {self.rule_profile['min_corridor_match_km']}km 才升高危")
                    p["corridor"] = "HIGHWAY"
                    corridor_type = "highway"
                    corridor_distance = hwy_d

                if speed >= self.rule_profile["hsr_speed_min_kmh"] and alt < self.rule_profile["ground_altitude_max_m"] and hsr_d < self.rule_profile["hsr_corridor_distance_m"]:
                    status = "SUSPICIOUS"
                    rules.append("GPS-R04-PARTIAL")
                    reason_parts.append(f"短段距高鐵路廊約 {hsr_d:.0f}m；需累積達 {self.rule_profile['min_corridor_match_km']}km 才升高危")
                    p["corridor"] = "HSR"
                    corridor_type = "hsr"
                    corridor_distance = hsr_d

                if speed < self.rule_profile["stationary_speed_max_kmh"]:
                    stationary_run += 1
                    if stationary_run >= self.rule_profile["stationary_points_min"]:
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
            segments.append({
                "start_idx": idx - 1,
                "end_idx": idx,
                "distance_m": dist,
                "speed_kmh": round(speed, 1),
                "corridor_type": corridor_type,
                "corridor_distance_m": corridor_distance,
                "rule_code": p["rule_code"],
            })
            if rules:
                segment_events.append({
                    "seq": p.get("seq", idx + 1),
                    "level": "CRITICAL" if status == "CRITICAL_FRAUD" else "WARNING",
                    "rule_code": p["rule_code"],
                    "message": p["anomaly_reason"],
                })

        corridor_runs = self._corridor_runs(points, segments)
        for run in corridor_runs:
            run_rule = "GPS-R03-CORRIDOR-RUN" if run["corridor_type"] == "highway" else "GPS-R04-CORRIDOR-RUN"
            for p in points:
                seq = int(p.get("seq") or 0)
                if run["start_seq"] < seq <= run["end_seq"]:
                    existing = [r for r in str(p.get("rule_code") or "").split(",") if r]
                    if run_rule not in existing:
                        existing.append(run_rule)
                    if "GPS-R03-PARTIAL" in existing:
                        existing.remove("GPS-R03-PARTIAL")
                    if "GPS-R04-PARTIAL" in existing:
                        existing.remove("GPS-R04-PARTIAL")
                    p["rule_code"] = ",".join(existing)
                    p["status"] = "CRITICAL_FRAUD"
                    p["anomaly_reason"] = run["reason"]
            segment_events.append({
                "seq": run["start_seq"],
                "level": "CRITICAL",
                "rule_code": ",".join(run["rule_codes"]),
                "message": run["reason"],
                "event": run,
            })

        critical = sum(1 for p in points if p.get("status") == "CRITICAL_FRAUD")
        suspicious = sum(1 for p in points if p.get("status") == "SUSPICIOUS")

        poaching_events = []
        for run in corridor_runs:
            prior_stationary = [
                p for p in points
                if int(p.get("seq") or 0) < int(run["start_seq"])
                and "GPS-R05" in str(p.get("rule_code") or "")
                and ("GPS-R08" in str(p.get("note") or "") or "GPS-R09" in str(p.get("note") or ""))
            ]
            if prior_stationary:
                event_point = prior_stationary[0]
                existing = [r for r in str(event_point.get("rule_code") or "").split(",") if r]
                for rule in ("GPS-R08", "GPS-R09", "GPS-R10"):
                    if rule not in existing:
                        existing.append(rule)
                event_point["rule_code"] = ",".join(existing)
                event_point["status"] = "SUSPICIOUS"
                event_point["event_icon"] = "🕸️"
                event_point["anomaly_reason"] = "疑似山區攔鳥網/擄鴿起點：低速滯留後接續交通工具路廊轉運"
                poaching_events.append({
                    "seq": event_point.get("seq"),
                    "lat": event_point.get("lat"),
                    "lng": event_point.get("lng"),
                    "level": "WARNING",
                    "rule_code": "GPS-R08,GPS-R09,GPS-R10",
                    "message": event_point["anomaly_reason"],
                })

        risk_score = min(100, critical * 5 + suspicious * 2 + max(0, gps_fault - 2) * 1)
        if corridor_runs:
            risk_score = max(risk_score, 88)
        elif suspicious >= 5:
            risk_score = max(risk_score, 45)
        elif gps_fault >= 2:
            risk_score = max(risk_score, 25)

        if any(r["corridor_type"] == "hsr" for r in corridor_runs):
            best = max((r for r in corridor_runs if r["corridor_type"] == "hsr"), key=lambda r: r["distance_km"])
            alerts.append({"type": "GPS-R04-CORRIDOR-RUN 高鐵連續路廊", "level": "CRITICAL", "message": best["reason"]})
        if any(r["corridor_type"] == "highway" for r in corridor_runs):
            best = max((r for r in corridor_runs if r["corridor_type"] == "highway"), key=lambda r: r["distance_km"])
            alerts.append({"type": "GPS-R03-CORRIDOR-RUN 高速連續路廊", "level": "CRITICAL", "message": best["reason"]})
        if not corridor_runs and any("GPS-R03-PARTIAL" in e["rule_code"] or "GPS-R04-PARTIAL" in e["rule_code"] for e in segment_events):
            alerts.append({"type": "GPS-R03/R04-PARTIAL 路廊短段待複核", "level": "WARNING", "message": f"偵測到短段貼近交通路廊，但未達連續 {self.rule_profile['min_corridor_match_km']}km 門檻，不直接判高危。"})
        if poaching_events:
            alerts.append({"type": "GPS-R08/R09/R10 疑似擄鴿/攔鳥網轉運起點", "level": "WARNING", "message": "偵測到低速滯留後接續交通工具路廊，已以 🕸️ 標記疑似攔鳥網/擄鴿起點，建議優先通知會長定位處理。"})
        if any("GPS-R05" in e["rule_code"] for e in segment_events):
            alerts.append({"type": "GPS-R05 中途滯留", "level": "WARNING", "message": "偵測到非歸巢點連續低速停留，需比對是否為 AB 舍、接應點或山區攔鳥網。"})
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
            "segment_events": segment_events[:100],
            "corridor_runs": corridor_runs,
            "poaching_events": poaching_events,
            "rules": RULES,
            "rule_profile": self.rule_profile,
            "critical_points_count": critical,
            "suspicious_points_count": suspicious,
            "gps_fault_points_count": gps_fault,
        }
