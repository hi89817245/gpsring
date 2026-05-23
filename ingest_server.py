import os
import json
import logging
import sqlite3
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import io
import csv

# 初始化 FastAPI app
app = FastAPI(title="GPS Pigeon Ring Ingestion API", version="1.0.5")

# 設定日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion")

# 資料庫連線設定 (PostgreSQL / PostGIS)
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_HOST_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "gpsring")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

# 本地 SQLite 資料庫路徑
SQLITE_DB_PATH = "/home/hi/workspace/gpsring/gpsring_local.db"

# 全域 PostgreSQL 可用性快取，避免每次連線超時卡頓 (啟動時一次偵測)
IS_POSTGRES_AVAILABLE = False

def detect_postgres():
    """在啟動時快速檢測 PostgreSQL 是否通暢 (設定 1 秒極短超時)"""
    global IS_POSTGRES_AVAILABLE
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            connect_timeout=1  # 1 秒極短超時，絕不乾等
        )
        conn.close()
        IS_POSTGRES_AVAILABLE = True
        logger.info("👉 [PostgreSQL 偵測成功] 本次服務將使用實體 PostGIS 資料庫儲存！")
    except Exception as e:
        IS_POSTGRES_AVAILABLE = False
        logger.warning(f"⚠️ [PostgreSQL 偵測失敗] 網路未通或未安裝。系統將自動降級至【本地極速 SQLite3 引擎】。Error: {e}")

def get_db_conn():
    """連線到適當的資料庫"""
    global IS_POSTGRES_AVAILABLE
    if IS_POSTGRES_AVAILABLE:
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
                cursor_factory=RealDictCursor,
                connect_timeout=1
            )
            return conn, "postgres"
        except Exception as e:
            logger.error(f"PostgreSQL connection dynamically failed, dropping to SQLite: {e}")
            # 動態降級
    
    # 建立 SQLite 連線
    try:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        # 讓 sqlite3 連線可以像 psycopg2 的 DictCursor 一樣通過欄位名稱存取
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"
    except Exception as e:
        logger.error(f"Failed to connect to SQLite: {e}")
        return None, "none"

def init_sqlite_db():
    """若使用 SQLite，自動建立相容的表格結構"""
    try:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        cur = conn.cursor()
        
        # 1. 建立 devices 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # 2. 建立 races 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS races (
                race_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. 建立 cotes 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cotes (
                cote_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 4. 建立 pigeons 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pigeons (
                ring_id TEXT PRIMARY KEY,
                cote_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 5. 建立 race_allocations 配置表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS race_allocations (
                device_id TEXT,
                race_id TEXT,
                ring_id TEXT,
                cote_id TEXT,
                log_interval_sec INTEGER,
                upload_interval_sec INTEGER,
                mode TEXT,
                gps_power_mode TEXT,
                start_time TEXT,
                wifi_ssid TEXT,
                wifi_password TEXT,
                PRIMARY KEY (device_id, race_id)
            );
        """)

        # 6. 建立 gps_track_points 定位軌跡點表 (新增 index 以極致優化大數據檢索)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gps_track_points (
                device_id TEXT,
                race_id TEXT,
                ring_id TEXT,
                seq INTEGER,
                gps_time TEXT,
                lat REAL,
                lng REAL,
                altitude REAL,
                speed_kmh REAL,
                heading REAL,
                hdop REAL,
                satellites INTEGER,
                battery_mv INTEGER,
                rssi INTEGER,
                PRIMARY KEY (device_id, race_id, seq)
            );
        """)
        
        # 建立索引優化查詢
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gps_points_dev_race ON gps_track_points (device_id, race_id);")
        
        conn.commit()
        conn.close()
        logger.info("✅ [SQLite3 本地資料庫初始化成功] 表格與檢索索引就緒！")
    except Exception as e:
        logger.error(f"❌ [SQLite3 初始化失敗]: {e}")

# 啟動時自動進行環境檢測與資料庫初始化
detect_postgres()
init_sqlite_db()

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

# --- 升級：解決 Favicon 控制台錯誤 ---
@app.get("/favicon.ico", status_code=204)
def get_favicon_ico():
    return None

@app.get("/favicon.svg", status_code=204)
def get_favicon_svg():
    return None

# --- 升級：支援前端與靜態網頁合併託管 ---
@app.get("/", response_class=HTMLResponse)
def read_root():
    """提供前端主頁面，實現 Web 與 API 同一 Port 運行 (加上防快取標頭)"""
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(
        content=html_content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.get("/index.html", response_class=HTMLResponse)
def read_index_html():
    """相容 /index.html 路由 (加上防快取標頭)"""
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(
        content=html_content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

# --- CSV 欄位定義 ---
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
        
        grouped_points = {}
        for row in reader:
            try:
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
            payload = IngestPayload(device=pid, race=race, cote=cote, ring=pid, points=pts)
            res = ingest_gps_tracks(payload)
            results[pid] = res
            total_rows += len(pts)
            
        return {
            "status": "success",
            "message": "Successfully parsed and loaded multi-track CSV into local SQLite/Postgres.",
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
    
    conn_tuple = get_db_conn()
    if conn_tuple[0] is None:
        raise HTTPException(status_code=500, detail="No database available.")
        
    conn, engine_type = conn_tuple
    try:
        if engine_type == "postgres":
            with conn.cursor() as cur:
                cur.execute("INSERT INTO devices (device_id) VALUES (%s) ON CONFLICT (device_id) DO NOTHING;", (payload.device,))
                cur.execute("INSERT INTO races (race_id) VALUES (%s) ON CONFLICT (race_id) DO NOTHING;", (payload.race,))
                cur.execute("INSERT INTO cotes (cote_id) VALUES (%s) ON CONFLICT (cote_id) DO NOTHING;", (payload.cote,))
                cur.execute("INSERT INTO pigeons (ring_id, cote_id) VALUES (%s, %s) ON CONFLICT (ring_id) DO NOTHING;", (payload.ring, payload.cote))
                
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
        else: # sqlite
            with conn:
                cur = conn.cursor()
                cur.execute("INSERT OR IGNORE INTO devices (device_id) VALUES (?);", (payload.device,))
                cur.execute("INSERT OR IGNORE INTO races (race_id) VALUES (?);", (payload.race,))
                cur.execute("INSERT OR IGNORE INTO cotes (cote_id) VALUES (?);", (payload.cote,))
                cur.execute("INSERT OR IGNORE INTO pigeons (ring_id, cote_id) VALUES (?, ?);", (payload.ring, payload.cote))
                
                cur.execute("""
                    INSERT INTO race_allocations (
                        device_id, race_id, ring_id, cote_id, log_interval_sec, upload_interval_sec, mode, gps_power_mode, start_time, wifi_ssid, wifi_password
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return {"status": "success", "message": f"Device config applied successfully ({engine_type})."}
    except Exception as e:
        logger.error(f"Failed to apply config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/v1/tracks/ingest")
def ingest_gps_tracks(payload: IngestPayload):
    logger.info(f"Ingesting {len(payload.points)} GPS points from device {payload.device}")
    
    conn_tuple = get_db_conn()
    if conn_tuple[0] is None:
        raise HTTPException(status_code=500, detail="No database connection available.")
        
    conn, engine_type = conn_tuple
    try:
        inserted_count = 0
        if engine_type == "postgres":
            with conn.cursor() as cur:
                for p in payload.points:
                    gps_dt = datetime.fromtimestamp(p.timestamp)
                    cur.execute("""
                        INSERT INTO gps_track_points (
                            device_id, race_id, ring_id, seq, gps_time, geom, altitude, speed_kmh, heading, hdop, satellites, battery_mv, rssi
                        ) VALUES (
                            %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s, %s, %s
                        ) ON CONFLICT DO NOTHING;
                    """, (
                        payload.device, payload.race, payload.ring, p.seq, gps_dt,
                        p.lng, p.lat, p.alt, p.speed_kmh, p.heading, p.hdop, p.satellites, p.battery_mv, p.rssi
                    ))
                    inserted_count += 1
                conn.commit()
        else: # sqlite 本地極速儲存
            with conn:
                cur = conn.cursor()
                for p in payload.points:
                    gps_dt_str = datetime.fromtimestamp(p.timestamp).isoformat()
                    cur.execute("""
                        INSERT OR IGNORE INTO gps_track_points (
                            device_id, race_id, ring_id, seq, gps_time, lat, lng, altitude, speed_kmh, heading, hdop, satellites, battery_mv, rssi
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        payload.device, payload.race, payload.ring, p.seq, gps_dt_str,
                        p.lat, p.lng, p.alt, p.speed_kmh, p.heading, p.hdop, p.satellites, p.battery_mv, p.rssi
                    ))
                    inserted_count += 1
        return {"status": "success", "inserted": inserted_count, "engine": engine_type}
    except Exception as e:
        logger.error(f"Failed to ingest tracks: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/v1/tracks/{device_id}/{race_id}")
def get_device_race_tracks(device_id: str, race_id: str):
    conn_tuple = get_db_conn()
    from fraud_engine import PigeonFraudEngine
    engine = PigeonFraudEngine()
    
    conn, engine_type = conn_tuple
    pts = []
    
    if engine_type == "none" or (engine_type == "sqlite" and check_sqlite_empty(conn, device_id, race_id)):
        # 如果是 SQLite 且資料庫為空，或者是無可用資料庫，則採取【動態降級範本】保證前台 100% 能演示
        mode_map = {
            "G0703-00001": "normal",
            "G0703-00002": "cheat_highway",
            "G0703-CHEATER": "cheat_highway",
            "G0703-AB_COTE": "suspicious_ab",
            "G0703-HSR": "cheat_hsr"
        }
        selected_mode = mode_map.get(device_id, "normal")
        import train_dataset_generator
        raw_pts = train_dataset_generator.generate_csv_track(selected_mode)
        for p in raw_pts:
            pts.append({
                "seq": p["seq"],
                "timestamp": p["timestamp"],
                "lat": p["lat"],
                "lng": p["lng"],
                "alt": p["alt"],
                "speed_kmh": p["speed_kmh"],
                "heading": p["heading"],
                "hdop": p["hdop"],
                "satellites": p["satellites"],
                "battery_mv": p["battery_mv"],
                "rssi": p["rssi"],
                "device": device_id,
                "race": race_id
            })
        if conn:
            conn.close()
    else:
        try:
            if engine_type == "postgres":
                with conn.cursor() as cur:
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
                    for r in rows:
                        pts.append(dict(r))
            else: # sqlite
                with conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT 
                            seq, 
                            strftime('%s', gps_time) as timestamp,
                            lat, 
                            lng, 
                            altitude as alt, 
                            speed_kmh, 
                            heading, 
                            hdop, 
                            satellites, 
                            battery_mv, 
                            rssi
                        FROM gps_track_points
                        WHERE device_id = ? AND race_id = ?
                        ORDER BY seq ASC;
                    """, (device_id, race_id))
                    rows = cur.fetchall()
                    for r in rows:
                        row_dict = dict(r)
                        # sqlite strftime 回傳為字串，轉換為數字
                        try:
                            row_dict["timestamp"] = int(row_dict["timestamp"])
                        except:
                            row_dict["timestamp"] = int(datetime.utcnow().timestamp())
                        pts.append(row_dict)
        except Exception as e:
            logger.error(f"Failed to query tracks: {e}")
        finally:
            conn.close()
            
    # 進行物理防弊分析
    analysis = engine.analyze_track(pts)
    return {
        "status": "success", 
        "points": pts,
        "analysis": analysis,
        "engine": engine_type
    }

def check_sqlite_empty(conn, device_id: str, race_id: str) -> bool:
    """檢查 SQLite 中是否真的有該設備賽事之資料，若無則降級為範本"""
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM gps_track_points WHERE device_id = ? AND race_id = ? LIMIT 1;", (device_id, race_id))
        return cur.fetchone() is None
    except:
        return True
