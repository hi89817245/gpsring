# GPS 鴿環專案資料庫與通訊規格書 (DB_SPEC.md)
Version: 1.0.0
Last Updated: 2026-05-20

本文件定義 GPS 鴿環系統 (gpsring) 的後台資料庫結構 (PostgreSQL / PostGIS) 與與硬體外包商之間的設定與傳輸 API 協議。

---

## 1. 系統架構簡介
本系統旨在接收 GPS 鴿環上傳之定位軌跡，並偵測賽鴿在飛行過程中的異常（如坐車、走高速公路、AB舍作弊等）。
- **實體層**: ESP32-C3 + GPS + eSIM (4G) / Wi-Fi。
- **測試階段**: 第一輪由合作商透過 Wi-Fi 將測試數據以 HTTP/JSON 形式 POST 上傳至後台 Ingestion API。
- **預設**：wifi ssid 及密碼為 gscc3 / 2965084522053 以便未來測試

---

## 2. API 規格協議

### 2.1 裝置設定 API (Device Configuration)
**Endpoint**: `POST /api/v1/devices/config`
**Description**: 設定或更新鴿環當前的賽事與運作參數。
**Payload (JSON)**:
```json
{
  "device": "G0703-00001",
  "race": "115D1X",
  "cote": "8888",
  "ring": "660001",
  "log_interval_sec": 10,
  "upload_interval_sec": 60,
  "mode": "offline|online|hybrid",
  "gps_power_mode": "normal|low_power",
  "start_time": "2026-05-20T07:30:00+08:00"
}
```

### 2.2 軌跡數據上報 API (GPS Trajectory Ingestion)
**Endpoint**: `POST /api/v1/tracks/ingest`
**Description**: 鴿環上傳多點 GPS 軌跡（支援批次上傳）。
**Payload (JSON)**:
```json
{
  "device": "G0703-00001",
  "race": "115D1X",
  "cote": "8888",
  "ring": "660001",
  "points": [
    {
      "seq": 1024,
      "timestamp": 1779210495,
      "lat": 23.480123,
      "lng": 120.450456,
      "alt": 120.5,
      "speed_kmh": 72.4,
      "heading": 183.2,
      "hdop": 1.1,
      "satellites": 12,
      "battery_mv": 3840,
      "rssi": -89
    }
  ]
}
```

---

## 3. 資料庫 Schema (PostgreSQL / PostGIS)

### 3.1 設備基本資料表 `devices`
```sql
CREATE TABLE devices (
    device_id VARCHAR(50) PRIMARY KEY, -- 鴿環實體 ID (例如 G0703-00001)
    status VARCHAR(20) DEFAULT 'idle',  -- idle, racing, testing
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 賽事基本資料表 `races`
```sql
CREATE TABLE races (
    race_id VARCHAR(50) PRIMARY KEY,     -- 賽事編號 (例如 115D1X)
    name VARCHAR(100),
    release_point GEOMETRY(Point, 4326), -- 放鴿點 (起點)
    release_time TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 鴿舍資料表 `cotes`
```sql
CREATE TABLE cotes (
    cote_id VARCHAR(50) PRIMARY KEY,    -- 鴿舍編號 (例如 8888)
    name VARCHAR(100),
    location GEOMETRY(Point, 4326),     -- 鴿舍經緯度 (終點)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

### 3.4 鴿子註冊表 `pigeons`
```sql
CREATE TABLE pigeons (
    ring_id VARCHAR(50) PRIMARY KEY,    -- 鴿子腳環號 (例如 660001)
    cote_id VARCHAR(50) REFERENCES cotes(cote_id),
    color VARCHAR(30),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

### 3.5 賽事綁定表 `race_allocations`
```sql
CREATE TABLE race_allocations (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(50) REFERENCES devices(device_id),
    race_id VARCHAR(50) REFERENCES races(race_id),
    ring_id VARCHAR(50) REFERENCES pigeons(ring_id),
    cote_id VARCHAR(50) REFERENCES cotes(cote_id),
    log_interval_sec INT DEFAULT 10,
    upload_interval_sec INT DEFAULT 60,
    mode VARCHAR(20) DEFAULT 'hybrid',
    gps_power_mode VARCHAR(20) DEFAULT 'normal',
    start_time TIMESTAMP WITH TIME ZONE,
    allocated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(device_id, race_id)
);
```

### 3.6 GPS 軌跡明細點表 `gps_track_points`
本表使用 PostGIS 的 `GEOMETRY(Point, 4326)` 進行地理編碼與空間索引。
```sql
CREATE TABLE gps_track_points (
    id BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(50) REFERENCES devices(device_id),
    race_id VARCHAR(50) REFERENCES races(race_id),
    ring_id VARCHAR(50) REFERENCES pigeons(ring_id),
    seq INT,                                      -- 硬體封包序號
    gps_time TIMESTAMP WITH TIME ZONE,            -- 定位時間
    geom GEOMETRY(Point, 4326),                   -- 經緯度
    altitude NUMERIC(6, 2),                       -- 海拔高度
    speed_kmh NUMERIC(5, 2),                      -- 速度
    heading NUMERIC(5, 2),                        -- 航向角
    hdop NUMERIC(3, 1),                           -- 水平精度因子
    satellites INT,                               -- 衛星數
    battery_mv INT,                               -- 電池電壓 (mV)
    rssi INT,                                     -- 信號強度
    received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 建立索引以優化查詢
CREATE INDEX idx_gps_track_points_geom ON gps_track_points USING GIST(geom);
CREATE INDEX idx_gps_track_points_lookup ON gps_track_points(device_id, race_id, gps_time DESC);
```

### 3.7 異常分析事件表 `fraud_alerts`
```sql
CREATE TABLE fraud_alerts (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(50) REFERENCES devices(device_id),
    race_id VARCHAR(50) REFERENCES races(race_id),
    ring_id VARCHAR(50) REFERENCES pigeons(ring_id),
    alert_type VARCHAR(50),                       -- 'HIGHWAY_MATCH', 'OVER_SPEED', 'AB_COTE_FRAUD', 'GPS_LOST_STATIONARY'
    risk_score INT,                               -- 0-100
    details JSONB,                                -- 存放引發警告的軌跡點/詳情
    detected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```
