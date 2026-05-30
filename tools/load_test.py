#!/usr/bin/env python3
"""
GPSRing 後台壓測腳本 (load_test.py)
用途：對 /api/v1/tracks/ingest 端點做並行壓測，驗證後台承載能力
用法：
  python3 tools/load_test.py                   # 預設：10 workers × 20 req 各 20 points
  python3 tools/load_test.py --workers 30 --requests 50 --points 50
  python3 tools/load_test.py --host http://192.168.120.218:8801  # 指定 API host
"""
import asyncio
import argparse
import json
import random
import time
import math
from datetime import datetime

import httpx

# ── 預設參數 ─────────────────────────────────────────────
DEFAULT_HOST     = "http://192.168.120.218:8801"
DEFAULT_WORKERS  = 10
DEFAULT_REQUESTS = 20   # 每 worker 送幾個 request
DEFAULT_POINTS   = 20   # 每 request 含幾個 GPS point

# 台灣中部鴿路航跡基準點 (仿真賽線)
BASE_LAT, BASE_LNG = 23.6978, 120.9605
DEVICE_PREFIX = "G0703"


# ── 假資料產生器 ──────────────────────────────────────────
def fake_track_payload(device_idx: int, race_id: str, n_points: int) -> dict:
    """產生一筆仿真 GPS 軌跡 payload"""
    device_id = f"{DEVICE_PREFIX}-LOAD{device_idx:03d}"
    base_ts   = int(time.time()) - n_points * 5

    points = []
    for i in range(n_points):
        # 模擬從放飛點往北飛行，加少量隨機擾動
        lat  = BASE_LAT + i * 0.001 + random.uniform(-0.0002, 0.0002)
        lng  = BASE_LNG + i * 0.0005 + random.uniform(-0.0001, 0.0001)
        points.append({
            "seq":        i + 1,
            "timestamp":  base_ts + i * 5,
            "lat":        round(lat, 6),
            "lng":        round(lng, 6),
            "alt":        round(150 + i * 0.5 + random.uniform(-5, 5), 1),
            "speed_kmh":  round(60 + random.uniform(-10, 10), 1),
            "heading":    round(random.uniform(0, 360), 1),
            "hdop":       round(random.uniform(0.8, 2.0), 1),
            "satellites": random.randint(8, 14),
            "battery_mv": random.randint(3600, 4200),
            "rssi":       random.randint(-90, -50),
        })

    return {
        "device": device_id,
        "race":   race_id,
        "cote":   f"COTE-{device_idx:03d}",
        "ring":   f"RING-{device_idx:03d}",
        "points": points,
    }


# ── 單 worker ─────────────────────────────────────────────
async def worker(worker_id: int, host: str, n_requests: int, n_points: int,
                 results: list, client: httpx.AsyncClient):
    race_id = f"LOADTEST-{datetime.now().strftime('%H%M%S')}"
    latencies = []
    errors = []

    for i in range(n_requests):
        payload = fake_track_payload(worker_id * 100 + i, race_id, n_points)
        t0 = time.perf_counter()
        try:
            resp = await client.post(
                f"{host}/api/v1/tracks/ingest",
                json=payload,
                timeout=10.0,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code == 200:
                latencies.append(elapsed_ms)
            else:
                errors.append(f"HTTP {resp.status_code}: {resp.text[:80]}")
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            errors.append(f"EXC {type(e).__name__}: {str(e)[:80]}")

    results.append({
        "worker_id": worker_id,
        "ok":        len(latencies),
        "errors":    len(errors),
        "error_msgs": errors[:3],
        "p50_ms":    round(sorted(latencies)[len(latencies)//2], 1) if latencies else None,
        "p95_ms":    round(sorted(latencies)[int(len(latencies)*0.95)], 1) if len(latencies) >= 5 else None,
        "max_ms":    round(max(latencies), 1) if latencies else None,
    })


# ── 主流程 ────────────────────────────────────────────────
async def run(host: str, n_workers: int, n_requests: int, n_points: int):
    print(f"\n{'='*60}")
    print(f"  GPSRing Load Test")
    print(f"  Host    : {host}")
    print(f"  Workers : {n_workers}")
    print(f"  Req/wkr : {n_requests}  |  Points/req: {n_points}")
    print(f"  Total   : {n_workers * n_requests} requests, "
          f"{n_workers * n_requests * n_points} GPS points")
    print(f"{'='*60}\n")

    results = []
    t_start = time.perf_counter()

    async with httpx.AsyncClient() as client:
        tasks = [
            worker(i, host, n_requests, n_points, results, client)
            for i in range(n_workers)
        ]
        await asyncio.gather(*tasks)

    elapsed = time.perf_counter() - t_start

    # 彙整
    total_ok  = sum(r["ok"] for r in results)
    total_err = sum(r["errors"] for r in results)
    all_p50   = [r["p50_ms"] for r in results if r["p50_ms"]]
    all_p95   = [r["p95_ms"] for r in results if r["p95_ms"]]
    all_max   = [r["max_ms"] for r in results if r["max_ms"]]

    print(f"{'─'*60}")
    print(f"  結果摘要")
    print(f"{'─'*60}")
    print(f"  總耗時   : {elapsed:.2f}s")
    print(f"  成功     : {total_ok} ({total_ok/(total_ok+total_err)*100:.1f}%)")
    print(f"  失敗     : {total_err}")
    print(f"  RPS      : {total_ok/elapsed:.1f} req/s")
    if all_p50:
        avg_p50 = sum(all_p50)/len(all_p50)
        avg_p95 = sum(all_p95)/len(all_p95) if all_p95 else 0
        worst   = max(all_max) if all_max else 0
        print(f"  p50 延遲 : {avg_p50:.1f} ms")
        print(f"  p95 延遲 : {avg_p95:.1f} ms")
        print(f"  最大延遲 : {worst:.1f} ms")
    print(f"{'─'*60}")

    # 顯示任何錯誤
    for r in results:
        if r["error_msgs"]:
            print(f"  [worker {r['worker_id']}] errors: {r['error_msgs']}")

    print()
    return {
        "ok": total_ok, "errors": total_err,
        "elapsed_s": round(elapsed, 2),
        "rps": round(total_ok/elapsed, 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPSRing load test")
    parser.add_argument("--host",     default=DEFAULT_HOST)
    parser.add_argument("--workers",  type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS)
    parser.add_argument("--points",   type=int, default=DEFAULT_POINTS)
    args = parser.parse_args()

    asyncio.run(run(args.host, args.workers, args.requests, args.points))
