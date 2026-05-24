import time

from fraud_engine import PigeonFraudEngine
import train_dataset_generator as gen


def make_point(seq, ts, lat, lng, speed=100.0, alt=50.0, note=""):
    return {
        "seq": seq,
        "timestamp": ts,
        "lat": lat,
        "lng": lng,
        "alt": alt,
        "speed_kmh": speed,
        "heading": 180,
        "hdop": 0.8,
        "satellites": 12,
        "battery_mv": 3950,
        "rssi": -70,
        "note": note,
    }


def test_short_highway_corridor_under_threshold_is_review_not_critical():
    """短暫貼近國道路廊不足 20km 時，不應直接判高危作弊。"""
    base = int(time.time()) - 3600
    pts = []
    seq = 1
    ts = base
    base_lat, base_lng, _name = gen.HIGHWAY_CORRIDOR[0]
    for offset in (0.0, 0.015, 0.030):  # 約 3km，刻意低於 20km 門檻
        pts.append(make_point(seq, ts, base_lat, base_lng - offset, speed=96.0, alt=55.0, note="SHORT_HIGHWAY_TOUCH"))
        seq += 1
        ts += 60

    engine = PigeonFraudEngine(rule_profile={"min_corridor_match_km": 20})
    analysis = engine.analyze_track(pts)

    assert analysis["status"] != "CRITICAL_FRAUD"
    assert analysis["risk_score"] < 80
    assert not any("GPS-R03-CORRIDOR-RUN" in a.get("type", "") for a in analysis["alerts"])
    assert any("GPS-R03-PARTIAL" in e.get("rule_code", "") for e in analysis["segment_events"])


def test_continuous_highway_corridor_over_threshold_is_critical():
    """連續貼近國道路廊超過 20km 時，才成立高危路廊證據。"""
    pts = gen.generate_csv_track("cheat_highway")
    engine = PigeonFraudEngine(rule_profile={"min_corridor_match_km": 20})
    analysis = engine.analyze_track(pts)

    assert analysis["status"] == "CRITICAL_FRAUD"
    assert analysis["risk_score"] >= 80
    assert analysis["corridor_runs"]
    highway_runs = [r for r in analysis["corridor_runs"] if r["corridor_type"] == "highway"]
    assert highway_runs
    assert max(r["distance_km"] for r in highway_runs) >= 20
    assert any("GPS-R03-CORRIDOR-RUN" in a.get("type", "") for a in analysis["alerts"])


def test_rule_profile_exposes_default_20km_threshold():
    engine = PigeonFraudEngine()
    analysis = engine.analyze_track(gen.generate_csv_track("normal"))
    assert analysis["rule_profile"]["min_corridor_match_km"] == 20
