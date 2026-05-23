import os
import json
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor

# 初始化 FastAPI app
app = FastAPI(title="GPS Pigeon Ring Ingestion API", version="1.0.0")

# 設定日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion")

# 資料庫連線設定
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_HOST_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "gpsring")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

def get_db_conn():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            cursor_factory=RealDictCursor
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        # 降級至記憶體模擬 (Mock) 用於本地開發
        return None

# --- Pydantic 驗證 Schema ---

class ConfigPayload(BaseModel):
    device: str = Field(..., json_schema_extra={"example": "G0703-00001"})
    race: str = Field(..., json_schema_extra={"example": "115D1X"})
    cote: str = Field(..., json_schema_extra={"example": "8888"})
    ring: str = Field(..., json_schema_extra={"example": "660001"})
    log_interval_sec: int = Field(10, ge=1)
    upload_interval_sec: int = Field(60, ge=1)
    mode: str = Field("hybrid", pattern="^(offline|online|hybrid)$")
    gps_power_mode: str = Field("normal", pattern="^(normal|low_power)$")
    start_time: str = Field(..., json_schema_extra={"example": "2026-05-20T07:30:00+08:00"})
    wifi_ssid: Optional[str] = Field("gscc3", json_schema_extra={"example": "gscc3"})
    wifi_password: Optional[str] = Field("2965084522053", json_schema_extra={"example": "2965084522053"})

class GPSPoint(BaseModel):
    seq: int
    timestamp: int
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    alt: float
    speed_kmh: float
    heading: float
    hdop: float
    satellites: int
    battery_mv: int
    rssi: int

class IngestPayload(BaseModel):
    device: str
    race: str
    cote: str
    ring: str
    points: List[GPSPoint]

# --- 模擬用記憶體儲存 (當無實體資料庫時備用) ---
MOCK_CONFIGS = {}
MOCK_TRACK_POINTS = []

# --- 升級：支援前端與靜態網頁合併託管 ---
@app.get("/", response_class=HTMLResponse)
def read_root():
    """提供前端主頁面，實現 Web 與 API 同一 Port 運行"""
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/index.html", response_class=HTMLResponse)
def read_index_html():
    """相容 /index.html 路由"""
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# --- 升級：支援 CSV 手動上傳與測試 ---
from fastapi import UploadFile, File
import io
import csv

CSV_FIELDS = ["id", "seq", "timestamp", "lat", "lng", "alt", "speed_kmh", "heading", "hdop", "satellites", "battery_mv", "rssi"]

@app.get("/api/v1/tracks/template/csv")
def download_csv_template(mode: str = "normal"):
    """提供測試用 CSV 樣板內容 (支援 mode: normal, cheat_highway, suspicious_ab, cheat_hsr)"""
    import train_dataset_generator
    points = train_dataset_generator.generate_csv_track(mode)
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_FIELDS)
    for p in points:
        writer.writerow([
            p.get("id", "G0703-00001"), p["seq"], p["timestamp"], p["lat"], p["lng"], p["alt"],
            p["speed_kmh"], p["heading"], p["hdop"], p["satellites"],
            p["battery_mv"], p["rssi"]
        ])
    
    # 解決 CORS 跨網域與直接下載，回傳純文字 CSV Response 讓瀏覽器直接觸發下載
    from fastapi.responses import Response
    response_content = output.getvalue()
    return Response(
        content=response_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=template_{mode}.csv",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "*"
        }
    )

@app.post("/api/v1/tracks/upload/csv")
async def upload_csv_tracks(
    device: str,
    race: str,
    cote: str,
    ring: str,
    file: UploadFile = File(...)
):
    """手動上傳 CSV 軌跡數據檔，直接寫入對應的 Pigeon Ring (支援單一 CSV 整合多個 ID 軌跡)"""
    try:
        content = await file.read()
        decoded = content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(decoded))
        
        # 將點按照 id (或預設傳入的 ring/device) 進行分類
        grouped_points = {}
        
        for row in reader:
            try:
                # 支援 CSV 中存在 'id' 或 'ring_id' 欄位，若無則降級使用 API query string 帶入的 ring 參數
                pid = row.get("id") or row.get("ring_id") or ring or device
                
                pt = GPSPoint(
                    seq=int(row["seq"]),
                    timestamp=int(row["timestamp"]),
                    lat=float(row["lat"]),
                    lng=float(row["lng"]),
                    alt=float(row["alt"]),
                    speed_kmh=float(row["speed_kmh"]),
                    heading=float(row["heading"]),
                    hdop=float(row["hdop"]),
                    satellites=int(row["satellites"]),
                    battery_mv=int(row["battery_mv"]),
                    rssi=int(row["rssi"])
                )
                
                if pid not in grouped_points:
                    grouped_points[pid] = []
                grouped_points[pid].append(pt)
                
            except (KeyError, ValueError) as err:
                logger.warning(f"CSV Row validation failed, skipping row: {row}. Error: {err}")
                continue
                
        if not grouped_points:
            raise HTTPException(status_code=400, detail="CSV file contained no valid GPS points matching schema.")
            
        results = {}
        total_rows = 0
        for pid, pts in grouped_points.items():
            # 對於每個分組好的 ID，進行寫入
            # 對應設備與 ring，若無傳入則設為相同 id
            payload = IngestPayload(device=pid, race=race, cote=cote, ring=pid, points=pts)
            res = ingest_gps_tracks(payload)
            results[pid] = res
            total_rows += len(pts)
            
        return {
            "status": "success",
            "message": f"Successfully parsed and loaded multi-track CSV.",
            "parsed_rows": total_rows,
            "tracks_count": len(grouped_points),
            "ingest_result": results
        }
    except Exception as e:
        logger.error(f"Failed to process uploaded CSV: {e}")
        raise HTTPException(status_code=500, detail=f"CSV processing failed: {str(e)}")

# --- 路由實作 ---

@app.post("/api/v1/devices/config")
def set_device_config(payload: ConfigPayload):
    logger.info(f"Received config update for device: {payload.device}")
    
    conn = get_db_conn()
    if conn is None:
        # Mock 儲存
        MOCK_CONFIGS[payload.device] = payload.model_dump()
        return {"status": "success", "message": "Config saved (Mock mode)", "data": payload}
        
    try:
        with conn.cursor() as cur:
            # 1. 確保設備、賽事、鴿舍與鴿子存在 (UPSERT)
            cur.execute("INSERT INTO devices (device_id) VALUES (%s) ON CONFLICT (device_id) DO NOTHING;", (payload.device,))
            cur.execute("INSERT INTO races (race_id) VALUES (%s) ON CONFLICT (race_id) DO NOTHING;", (payload.race,))
            cur.execute("INSERT INTO cotes (cote_id) VALUES (%s) ON CONFLICT (cote_id) DO NOTHING;", (payload.cote,))
            cur.execute("INSERT INTO pigeons (ring_id, cote_id) VALUES (%s, %s) ON CONFLICT (ring_id) DO NOTHING;", (payload.ring, payload.cote))
            
            # 2. 寫入賽事分配關係
            cur.execute("""
                INSERT INTO race_allocations (
                    device_id, race_id, ring_id, cote_id, log_interval_sec, upload_interval_sec, mode, gps_power_mode, start_time, wifi_ssid, wifi_password
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_id, race_id) DO UPDATE SET
                    ring_id = EXCLUDED.ring_id,
                    cote_id = EXCLUDED.cote_id,
                    log_interval_sec = EXCLUDED.log_interval_sec,
                    upload_interval_sec = EXCLUDED.upload_interval_sec,
                    mode = EXCLUDED.mode,
                    gps_power_mode = EXCLUDED.gps_power_mode,
                    start_time = EXCLUDED.start_time,
                    wifi_ssid = EXCLUDED.wifi_ssid,
                    wifi_password = EXCLUDED.wifi_password;
            """, (
                payload.device, payload.race, payload.ring, payload.cote,
                payload.log_interval_sec, payload.upload_interval_sec,
                payload.mode, payload.gps_power_mode, payload.start_time,
                payload.wifi_ssid, payload.wifi_password
            ))
            conn.commit()
        return {"status": "success", "message": "Device configuration applied successfully."}
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to apply config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/v1/tracks/ingest")
def ingest_gps_tracks(payload: IngestPayload):
    logger.info(f"Received {len(payload.points)} GPS points from device {payload.device}")
    
    conn = get_db_conn()
    if conn is None:
        # Mock 儲存
        for p in payload.points:
            pt_dict = p.model_dump()
            pt_dict['device'] = payload.device
            pt_dict['race'] = payload.race
            pt_dict['ring'] = payload.ring
            MOCK_TRACK_POINTS.append(pt_dict)
        return {"status": "success", "inserted": len(payload.points), "mode": "Mock mode"}
        
    try:
        inserted_count = 0
        with conn.cursor() as cur:
            for p in payload.points:
                gps_dt = datetime.fromtimestamp(p.timestamp)
                # 使用 PostGIS ST_SetSRID(ST_MakePoint(lng, lat), 4326) 轉換為幾何點
                cur.execute("""
                    INSERT INTO gps_track_points (
                        device_id, race_id, ring_id, seq, gps_time, geom, altitude, speed_kmh, heading, hdop, satellites, battery_mv, rssi
                    ) VALUES (
                        %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT DO NOTHING; -- 避免重複上傳重複點
                """, (
                    payload.device, payload.race, payload.ring, p.seq, gps_dt,
                    p.lng, p.lat, p.alt, p.speed_kmh, p.heading, p.hdop, p.satellites, p.battery_mv, p.rssi
                ))
                inserted_count += 1
            conn.commit()
        return {"status": "success", "inserted": inserted_count}
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to ingest tracks: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/v1/tracks/{device_id}/{race_id}")
def get_device_race_tracks(device_id: str, race_id: str):
    conn = get_db_conn()
    from fraud_engine import PigeonFraudEngine
    engine = PigeonFraudEngine()
    
    if conn is None:
        # 從 Mock 撈資料
        pts = [p for p in MOCK_TRACK_POINTS if p['device'] == device_id and p['race'] == race_id]
        # 進行物理防弊分析
        analysis = engine.analyze_track(pts)
        return {
            "status": "success", 
            "points": pts,
            "analysis": analysis
        }
        
    try:
        with conn.cursor() as cur:
            # 使用 ST_X/ST_Y 讀出經緯度
            cur.execute("""
                SELECT 
                    seq, 
                    EXTRACT(EPOCH FROM gps_time)::INT as timestamp,
                    ST_Y(geom) as lat, 
                    ST_X(geom) as lng, 
                    altitude as alt, 
                    speed_kmh, 
                    heading, 
                    hdop, 
                    satellites, 
                    battery_mv, 
                    rssi
                FROM gps_track_points
                WHERE device_id = %s AND race_id = %s
                ORDER BY seq ASC;
            """, (device_id, race_id))
            rows = cur.fetchall()
            
            # Python DB API 返回的 RealDictCursor 是 dict，我們需要將它傳給防弊引擎進行計算
            analysis = engine.analyze_track(rows)
            
        return {
            "status": "success", 
            "points": rows,
            "analysis": analysis
        }
    except Exception as e:
        logger.error(f"Failed to retrieve tracks: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    # 預設綁定 0.0.0.0 確保 LAN (Windows 11) 可直連測試
    uvicorn.run(app, host="0.0.0.0", port=8801)
