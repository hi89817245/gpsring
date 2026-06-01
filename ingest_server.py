import os
import json
import logging
import sqlite3
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import mimetypes
from pydantic import BaseModel, Field
from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import io
import csv

FIRMWARE_DIR = os.getenv("GPSRING_FIRMWARE_DIR", "/home/hi/workspace/gpsring/firmware")

# 初始化 FastAPI app
app = FastAPI(title="GPS Pigeon Ring Ingestion API", version="1.1.0")

# ── 裝置 Heartbeat 記憶體快取（輕量即時監控，不寫 DB）────────────────
# { device_id: { "state": str, "ip": str, "fw": str, "mac": str, "ts": float, "gps_seen": bool, "gps_fixed": bool } }
_device_heartbeats: dict = {}

# ── WebSocket 廣播管理 ──────────────────────────────────────
_ws_clients: list[WebSocket] = []

async def _ws_broadcast(data: dict):
    """廣播裝置狀態到所有連線的 WebSocket 客戶端"""
    if not _ws_clients:
        return
    import json
    msg = json.dumps(data, ensure_ascii=False)
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# CORS-aware firmware file route (取代 StaticFiles mount，確保 CORS header 正確傳遞)
@app.options("/firmware/{filename:path}")
async def firmware_options(filename: str):
    return Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })

@app.get("/firmware/{filename:path}")
async def firmware_file(filename: str):
    """提供 /firmware 下的檔案，並加 CORS header 供 216 ESPConnect 跨域存取"""
    fpath = os.path.join(FIRMWARE_DIR, filename)
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="firmware file not found")
    mime, _ = mimetypes.guess_type(fpath)
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    return FileResponse(fpath, media_type=mime or "application/octet-stream", headers=headers)

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

# --- RingOps 鴿環作業站 ---
@app.get("/otg", response_class=HTMLResponse)
def ringops_otg():
    """RingOps 鴿環作業站 Web UI"""
    with open("otg.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(
        content=html_content,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )

@app.get("/api/v1/firmware/list")
def firmware_list():
    """列出 firmware/ 目錄下的 .bin 檔案清單"""
    import os
    fw_dir = os.path.join(os.path.dirname(__file__), "firmware")
    try:
        files = sorted([f for f in os.listdir(fw_dir) if f.endswith(".bin")])
    except FileNotFoundError:
        files = []
    return {"files": files, "base_url": "/firmware/"}


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
        rows = list(reader)
        csv_ids = {row.get("id") or row.get("ring_id") for row in rows if (row.get("id") or row.get("ring_id"))}
        single_track_upload = len(csv_ids) <= 1
        
        grouped_points = {}
        for row in rows:
            try:
                # 單一軌跡上傳以表單設備 ID 為準，符合測試者「填哪個 device 就載入哪個」的直覺；
                # 只有真正多 ID CSV 才保留每列 id/ring_id 做多軌匯入。
                row_pid = row.get("id") or row.get("ring_id") or ring or device
                pid = (device or row_pid) if single_track_upload else row_pid
                
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
            "track_ids": list(grouped_points.keys()),
            "primary_track_id": next(iter(grouped_points.keys())),
            "ingest_result": results
        }
    except Exception as e:
        logger.error(f"Failed to process uploaded CSV: {e}")
        raise HTTPException(status_code=500, detail=f"CSV processing failed: {str(e)}")

# --- 路由實作 ---

# ── 裝置即時狀態監控 ───────────────────────────────────────────
class HeartbeatPayload(BaseModel):
    device_id: str
    state: str = "standby"
    firmware_version: str = ""
    build_hash: str = ""
    mac: str = ""
    ip: str = ""
    gps_seen: bool = False
    gps_fixed: bool = False
    boot_count: int = 0
    free_heap: int = 0
    battery_raw: int = 0
    factory_id: int = 0
    lat: float = 0.0
    lon: float = 0.0
    satellites: int = 0

# NFC 配對：GPS鴿環 ↔ 鴿環號配對表（記憶體快取；未來可持久化至 DB）
# key: mac 或 device_id, value: {ringno1, factory_id, paired_at, ...}
_nfc_pairings: dict = {}

class NfcPairPayload(BaseModel):
    device_id: str = ""
    mac: str = ""
    factory_id: int = 0
    ringno1: str          # 第一鴿環號（原始賽鴿環號）
    action: str = "pair"  # pair | checkin | checkout

@app.post("/api/v1/devices/heartbeat")
async def receive_heartbeat(payload: HeartbeatPayload):
    """MCU 定期上報心跳 — 存入記憶體快取，含 lat/lon/factory_id，並廣播 WebSocket"""
    key = payload.mac or payload.device_id
    _device_heartbeats[key] = {
        "state": payload.state,
        "firmware_version": payload.firmware_version,
        "build_hash": payload.build_hash,
        "mac": payload.mac,
        "ip": payload.ip,
        "gps_seen": payload.gps_seen,
        "gps_fixed": payload.gps_fixed,
        "boot_count": payload.boot_count,
        "free_heap": payload.free_heap,
        "battery_raw": payload.battery_raw,
        "factory_id": payload.factory_id,
        "lat": payload.lat,
        "lon": payload.lon,
        "satellites": payload.satellites,
        "last_seen": time.time(),
        # 若已配對，附加 ringno1
        "ringno1": _nfc_pairings.get(key, {}).get("ringno1", ""),
    }
    logger.info(f"[HEARTBEAT] {key} state={payload.state} ip={payload.ip} lat={payload.lat} lon={payload.lon}")
    # 即時廣播到 WebSocket 訂閱者
    await _ws_broadcast({"type": "heartbeat", "device_id": key, **_device_heartbeats[key], "last_seen_iso": datetime.fromtimestamp(_device_heartbeats[key]["last_seen"]).strftime("%Y-%m-%d %H:%M:%S")})
    return {"status": "ok", "device_id": key}

@app.post("/api/v1/devices/nfc_pair")
def nfc_pair(payload: NfcPairPayload):
    """NFC 配對：GPS鴿環 ↔ 第一鴿環環號
    action=pair    → 出廠配對（factory_id + ringno1 綁定）
    action=checkin → 上車感應，觸發後台通知 MCU 切換 CARING（未來擴充）
    action=checkout→ 鴿返，解除 caring 狀態
    """
    key = payload.mac or payload.device_id
    if not key:
        raise HTTPException(status_code=400, detail="device_id or mac required")
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec = _nfc_pairings.get(key, {})
    if payload.action in ("pair", "checkin"):
        rec.update({
            "device_id": payload.device_id,
            "mac": payload.mac,
            "factory_id": payload.factory_id,
            "ringno1": payload.ringno1,
            "action": payload.action,
            "updated_at": now_iso,
        })
        _nfc_pairings[key] = rec
        # 若裝置在線，同步更新 heartbeat 快取的 ringno1
        if key in _device_heartbeats:
            _device_heartbeats[key]["ringno1"] = payload.ringno1
            if payload.action == "checkin":
                _device_heartbeats[key]["state"] = "caring"
    elif payload.action == "checkout":
        rec["action"] = "checkout"
        rec["updated_at"] = now_iso
        _nfc_pairings[key] = rec
    logger.info(f"[NFC] {payload.action} device={key} ringno1={payload.ringno1}")
    return {"status": "ok", "action": payload.action, "device": key, "ringno1": payload.ringno1}

@app.get("/api/v1/devices/nfc_pairs")
def list_nfc_pairs():
    """列出所有 NFC 配對記錄"""
    return {"total": len(_nfc_pairings), "pairs": list(_nfc_pairings.values())}


# ── factory_id 自動分配（每台新板唯一序號）─────────────────────
_factory_id_counter: int = 0  # 將在 startup 從 DB 最大值初始化

def _init_factory_counter():
    global _factory_id_counter
    try:
        conn, dbtype = get_db_conn()
        if conn is None:
            return
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(factory_id), 0) FROM nfc_pairings")
        row = cur.fetchone()
        _factory_id_counter = (row[0] if row else 0)
        conn.close()
    except Exception:
        pass  # DB 未就緒時從 0 開始

@app.on_event("startup")
async def _on_startup():
    _init_factory_counter()

@app.get("/api/v1/factory/next-id")
def get_next_factory_id():
    """新板首次開機取得唯一 factory_id（心跳帶回後存 NVS）"""
    global _factory_id_counter
    _factory_id_counter += 1
    fid = _factory_id_counter
    return {"factory_id": fid, "offset_lat_deg": round(0.0000899 * (fid - 1), 8)}


@app.get("/api/v1/devices/status")
def get_devices_status():
    """即時顯示所有裝置心跳狀態，包含在線/離線判斷（>60s 無心跳 = 離線）"""
    now = time.time()
    result = []
    for dev_id, info in _device_heartbeats.items():
        age_sec = int(now - info["last_seen"])
        online = age_sec < 60
        result.append({
            "device_id": dev_id,
            "online": online,
            "age_sec": age_sec,
            **info,
            "last_seen_iso": datetime.fromtimestamp(info["last_seen"]).strftime("%Y-%m-%d %H:%M:%S"),
        })
    # 依 state 排序：racing > caring > gps_fixed > gps_searching > standby > offline
    _state_order = {"racing": 0, "caring": 1, "gps_fixed": 2, "gps_searching": 3, "standby": 4, "init": 4}
    result.sort(key=lambda x: (_state_order.get(x["state"], 9) if x["online"] else 10, x["device_id"]))
    return {"total": len(result), "online": sum(1 for x in result if x["online"]), "devices": result}

@app.websocket("/ws/devices")
async def ws_devices(websocket: WebSocket):
    """WebSocket endpoint — 訂閱後每次 heartbeat 即時推送裝置狀態"""
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        # 連線後立即推送目前全部裝置快照
        now = time.time()
        snapshot = []
        for dev_id, info in _device_heartbeats.items():
            age_sec = int(now - info["last_seen"])
            snapshot.append({"type": "heartbeat", "device_id": dev_id, "online": age_sec < 60, "age_sec": age_sec, **info,
                              "last_seen_iso": datetime.fromtimestamp(info["last_seen"]).strftime("%Y-%m-%d %H:%M:%S")})
        import json
        await websocket.send_text(json.dumps({"type": "snapshot", "devices": snapshot}, ensure_ascii=False))
        # 保持連線，等待客戶端關閉
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


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
def get_device_race_tracks(
    device_id: str,
    race_id: str,
    min_corridor_match_km: float = Query(20, ge=1, le=200),
    highway_corridor_distance_m: float = Query(900, ge=50, le=5000),
    hsr_corridor_distance_m: float = Query(1200, ge=50, le=5000),
    ground_altitude_max_m: float = Query(90, ge=0, le=1000),
    highway_speed_min_kmh: float = Query(72, ge=1, le=250),
    hsr_speed_min_kmh: float = Query(150, ge=1, le=400),
):
    conn_tuple = get_db_conn()
    from fraud_engine import PigeonFraudEngine
    rule_profile = {
        "min_corridor_match_km": min_corridor_match_km,
        "highway_corridor_distance_m": highway_corridor_distance_m,
        "hsr_corridor_distance_m": hsr_corridor_distance_m,
        "ground_altitude_max_m": ground_altitude_max_m,
        "highway_speed_min_kmh": highway_speed_min_kmh,
        "hsr_speed_min_kmh": hsr_speed_min_kmh,
    }
    engine = PigeonFraudEngine(rule_profile=rule_profile)
    
    conn, engine_type = conn_tuple
    pts = []
    
    demo_mode_map = {
        "G0703-00001": "normal",
        "G0703-00002": "cheat_highway",
        "G0703-CHEATER": "cheat_highway",
        "G0703-AB_COTE": "suspicious_ab",
        "G0703-HSR": "cheat_hsr",
        "G0703-FAULT": "gps_fault",
        "G0703-NET": "poaching_mountain_net",
    }
    force_demo_template = device_id in demo_mode_map

    if force_demo_template or engine_type == "none" or (engine_type == "sqlite" and check_sqlite_empty(conn, device_id, race_id)):
        # Demo button 與保底模式一律使用新版真實沙盤範本，避免舊 SQLite 測試資料蓋掉展示結果。
        selected_mode = demo_mode_map.get(device_id, "normal")
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
                "note": p.get("note", ""),
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
