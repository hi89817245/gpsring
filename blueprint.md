# GPS 鴿環防弊系統 Blueprint

> **For Hermes:** Use `gps-ring-development`, `writing-plans`, and `subagent-driven-development` before implementing this blueprint.

**文件版本:** v0.3.0-blueprint  
**最後更新:** 2026-05-24 10:29 CST  
**目前實作基準:** v0.2.1「真實防弊沙盤 - 上傳抽稀修正版」  
**目標:** 從展示型 GPS 沙盤升級為可供鴿會快速定位作弊/擄鴿熱點的實務防弊平台。

---

## 1. 核心產品判斷

目前 v0.2.1 已能完成：CSV 上傳、低頻抽稀、點位 tooltip、分段紅/橘/紫/綠判定、拖曳沙盒、版本顯示。

但使用者指出正確方向：

1. **單點貼近高速/高鐵不夠構成作弊證據**。若只是幾個點靠近路線，不代表有交通工具作弊；真正有價值的證據應是「連續 N 公里」貼近高速公路/高鐵路廊，預設 N=20km。
2. **20km 是預設，不是寫死**。`min_corridor_match_km=20` 必須列入 Rule Profile（規則設定檔），可依不同賽距、地形、採樣率調整。
3. **作弊點不一定在基隆或 A/B 舍**。不法擄鴿集團可能在山區架設攔鳥網，真正要找的是「異常起點」與「滯留/轉運起點」，應用醒目圖示標示，方便會長快速處理。
4. **異常滯留需分流判斷**。未歸返前連續訊號但座標/速度幾乎不動且超過 30 分鐘，偏向擄鴿/攔鳥網；短暫停留約 10 分鐘內後接公路/高速/高鐵，偏向 A/B 舍或接應轉運。
5. **只看平面地圖不足以分析軌跡**。需要高度/時間維度小視窗，與大地圖互動比對位置、速度、高度、時間。
6. **規則碼不能整條路都一樣**。GPS-R01/R02/R03 應依區段與證據類型精準標註，並提供 help 與可調閥值。
7. **需要篩選器**。例如日期時間、GPS 高度 N 米以下、速度門檻、HDOP、衛星數、規則碼、風險等級，以便快速找出不尋常軌跡。
8. **韌體與後台需共用狀態**。外包韌體也應輸出 `init / caring / start` 狀態，供後台自檢與快速判斷是否符合公司標準。

---

## 2. v0.3.0 目標功能

### 2.1 連續路廊證據，不再用單點誤判

#### 需求
高速公路/高鐵作弊不可只看單點距離。必須累積連續區段長度。

#### 預設參數
| 參數 | 預設 | 說明 |
|---|---:|---|
| `min_corridor_match_km` | 20 km | 至少連續 20 公里貼近高速/高鐵才升為高危；預設值，可在閥值設定中調整 |
| `highway_corridor_distance_m` | 900 m | 高速/快速道路路廊容許距離 |
| `hsr_corridor_distance_m` | 1200 m | 高鐵路廊容許距離 |
| `ground_altitude_max_m` | 90 m | 貼地高速高度門檻 |
| `highway_speed_min_kmh` | 72 km/h | 疑似車輛速度下限 |
| `hsr_speed_min_kmh` | 150 km/h | 疑似高鐵速度下限 |
| `poaching_stationary_minutes` | 30 min | 擄鴿/攔鳥網：未歸返前長時間幾乎不動 |
| `ab_stationary_minutes` | 10 min | A/B 舍/接應：短暫停留後轉運 |
| `stationary_radius_m` | 30 m | 幾乎不動的座標半徑 |

#### 判定邏輯
1. 逐 segment 計算：
   - segment distance
   - segment speed
   - segment altitude
   - distance to highway corridor
   - distance to HSR corridor
2. 對符合條件的 segment 做 run-length grouping（連續區段合併）。
3. 若同一 run 的累積距離 >= 20km，標為：
   - `GPS-R03-CORRIDOR-RUN` 高速/快速道路連續路廊證據
   - `GPS-R04-CORRIDOR-RUN` 高鐵連續路廊證據
4. 若不足 20km，只能標成：
   - `GPS-R03-PARTIAL` / `GPS-R04-PARTIAL`
   - 風險等級維持 WARNING 或 REVIEW，不直接 CRITICAL。

#### UI 顯示
- 紅色線段只畫在連續證據成立的 run。
- Tooltip 顯示：
  - 起點 seq/time
  - 終點 seq/time
  - 連續貼近距離 km
  - 平均距路廊 m
  - 最高/平均速度
  - 高度範圍
  - 觸發規則

---

### 2.2 擄鴿/攔鳥網熱點：作弊起點圖示

#### 需求
不法擄鴿可能發生在山區、稜線、谷口、放飛後入陸路徑，不一定在基隆或 A/B 舍。系統的價值是快速找出「疑似被攔截/轉運起點」。

#### 新規則
| 規則 | 名稱 | 說明 |
|---|---|---|
| GPS-R08 | 疑似擄鴿/攔鳥網起點 | 正常飛行後突然低速/停留/貼地，接著出現車輛/高鐵/異常轉運 |
| GPS-R09 | 山區異常滯留 | 在非鴿舍、非合法檢查點、山區/稜線附近長時間低速 |
| GPS-R10 | 轉運起點 | 從低速滯留點之後出現連續路廊交通工具證據 |

#### 偵測邏輯
1. 找出正常飛行段後的第一次異常轉折：
   - speed < 5km/h 持續 N 分鐘
   - altitude 接近地形或突然下降
   - heading/速度行為突變
2. 若滯留後接交通工具 run，則該點標為「轉運起點」。
3. 若該點位於山區/非城鎮/非登記點，升級為「疑似攔鳥網/擄鴿點」。

#### 圖示
| 圖示 | 用途 |
|---|---|
| 🚩 紅旗 | 高危作弊起點 |
| 🕸️ 網 | 疑似攔鳥網/擄鴿點 |
| 🏚️ 小屋 | 疑似 A/B 舍或中繼舍 |
| 🚗 車 | 疑似車輛轉運起點 |
| 🚄 高鐵 | 疑似高鐵轉運段起點 |
| 🟣 衛星警示 | GPS 設備/定位異常起點 |

#### 模擬資料新增
新增範本：

```text
poaching_mountain_net
```

劇本：
1. 基隆/外海正常放飛。
2. 進入新北/桃園/苗栗丘陵或山區路徑。
3. 山區某點突然低速或停留，疑似攔鳥網。
4. 短暫滯留後沿山路/快速道路接駁。
5. 之後才接高速或放回。

---

### 2.3 高度/時間維度小視窗

#### 需求
加入一個小視窗，顯示時間序列圖，與大地圖互動。

#### 圖表內容
建議用一個 `bottom timeline panel` 或右下浮動 panel：

- X 軸：時間
- Y 軸 1：高度 alt
- Y 軸 2：速度 speed
- 背景色：依規則碼分段標色
- 可選顯示：HDOP、satellites、battery_mv

#### 互動
1. 滑鼠移到地圖點：timeline 同步高亮該時間點。
2. 滑鼠移到 timeline：地圖 marker 同步高亮。
3. 點擊異常 run：地圖 zoom 到該 run。
4. 點擊「作弊起點」：timeline 跳到該點。

#### 技術選型
優先用輕量方案：

- Chart.js 或 Apache ECharts
- 若要極簡，不引套件可先用 SVG polyline

---

### 2.4 規則 Help 與可調閥值

#### 需求
使用者與會長需要知道 GPS-R01/GPS-R02/GPS-R03 代表什麼、為何觸發、是否可調。

#### UI
新增「規則 Help / 閥值設定」面板：

| 欄位 | 說明 |
|---|---|
| 規則碼 | GPS-R01 等 |
| 中文名稱 | 超物理速度 / 貼地高速等 |
| 說明 | 用人話說明為何可疑 |
| 預設閥值 | 例如 20km、900m、72km/h |
| 可調欄位 | input number / slider |
| 套用範圍 | 前端沙盒 / 後端分析 |

#### 注意
- 調整閥值後應立即重跑前端分析。
- 後端也應接受 rule profile，避免前後端不一致。
- 需提供「恢復預設」按鈕。

---

### 2.5 快速篩選器（v0.3.1 已上線第一階段）

#### 需求
提供篩選器快速找異常軌跡，不要只能肉眼看全部。

#### 第一階段篩選條件
| 篩選 | 例子 |
|---|---|
| 日期時間 | 比賽開始～歸返期間 |
| 高度 | alt < N 米，例如 90m |
| 速度 | speed > N km/h |
| 規則碼 | 只看 GPS-R03/GPS-R08 |
| 風險 | CRITICAL / WARNING / GPS_FAULT |
| HDOP | hdop >= 3.5 |
| 衛星數 | satellites <= 5 |
| 連續路廊距離 | corridor_run_km >= 20 |
| 滯留時間 | stationary_minutes >= N |

#### UI 行為
- v0.3.1 已在左側新增「高度/時間小窗與異常篩選器」。
- 篩選後地圖只繪製符合條件的 segments/markers，未符合的小窗 bar 會降低透明度。
- 左側列表顯示符合條件的異常事件。
- 點擊高度/時間小窗 bar 或事件列表，可 zoom 到該定位點並打開 popup。
- 第一階段已支援：起訖時間、高度上下限、最低速度、風險等級、只看異常、規則碼/關鍵字。
- 待後續強化：HDOP/衛星數獨立欄位、連續路廊距離與滯留時間以 segment event 形式篩選。

---

## 3. 資料結構建議

### 3.1 SegmentAnalysis

```json
{
  "start_seq": 101,
  "end_seq": 112,
  "start_time": 1779580000,
  "end_time": 1779580600,
  "distance_km": 21.4,
  "avg_speed_kmh": 103.2,
  "max_speed_kmh": 108.7,
  "min_alt_m": 36.0,
  "max_alt_m": 72.0,
  "avg_corridor_distance_m": 210.0,
  "corridor_type": "highway",
  "status": "CRITICAL_FRAUD",
  "rule_codes": ["GPS-R02", "GPS-R03-CORRIDOR-RUN"],
  "reason": "連續 21.4km 貼近高速/快速道路，且低高度高速移動"
}
```

### 3.2 FraudEvent

```json
{
  "event_id": "EVT-001",
  "type": "POACHING_START",
  "icon": "🕸️",
  "seq": 76,
  "timestamp": 1779578000,
  "lat": 24.92,
  "lng": 121.05,
  "alt": 180,
  "risk": "CRITICAL",
  "rule_codes": ["GPS-R08", "GPS-R10"],
  "summary": "疑似山區攔鳥網/擄鴿後轉運起點",
  "next_segment_id": "SEG-003"
}
```

### 3.3 RuleProfile

```json
{
  "min_corridor_match_km": 20,
  "highway_corridor_distance_m": 900,
  "hsr_corridor_distance_m": 1200,
  "ground_altitude_max_m": 90,
  "highway_speed_min_kmh": 72,
  "hsr_speed_min_kmh": 150,
  "stationary_speed_max_kmh": 5,
  "stationary_minutes_min": 5,
  "gps_fault_hdop_min": 3.5,
  "gps_fault_satellites_max": 5
}
```

---

## 4. 實作分期

### Phase A：判定引擎升級

**目標:** 後端與前端都改為 segment/run-based analysis。

任務：
1. 新增 RuleProfile 常數與 API 回傳。
2. 後端 `fraud_engine.py` 增加 segment 分析。
3. 增加 corridor run grouping，計算連續貼近距離。
4. GPS-R03/GPS-R04 改為 run 成立才 CRITICAL。
5. 前端讀取 segment events 畫線與 tooltip。

驗收：
- 高速/高鐵需連續 >=20km 才紅線高危。
- 短暫靠近高速/高鐵只顯示 REVIEW/WARNING。

### Phase B：擄鴿/攔鳥網劇本與圖示

**目標:** 找出作弊/擄鴿起點，而不是只判整條線有問題。

任務：
1. 新增 `poaching_mountain_net` 範本。
2. 新增 GPS-R08/R09/R10。
3. 地圖新增 🕸️/🚩/🚗/🚄 圖示。
4. 左側事件列表新增「疑似起點」。

驗收：
- 上傳範本後地圖會清楚標出疑似攔鳥網點。
- 點擊事件會 zoom 到該點。

### Phase C：高度/時間視窗（尚未完成，列為下一個 UI 重點）

**目標:** 快速比對時間、高度、速度與地圖位置。

任務：
1. 新增 timeline panel。
2. 顯示 alt/speed 曲線。
3. 支援 hover cross-highlight。
4. 支援點擊異常事件跳轉。
5. 支援切換 HDOP/衛星數，分辨 GPS 故障與真作弊。

驗收：
- hover 地圖點時 timeline 高亮同一 seq。
- hover timeline 時地圖 marker 高亮。
- 可用高度/速度曲線看出「貼地高速」、「山區長時間不動」、「GPS 品質異常」。

### Phase D：Help 與閥值設定

**目標:** 讓會長與工程師都看得懂，並可調整規則。

任務：
1. 新增規則 help modal。
2. 新增閥值設定面板。
3. 前端即時套用。
4. 後端支援 rule profile。

驗收：
- 調整 `min_corridor_match_km` 後，紅線/警示即時變化。
- Help 可清楚說明每條 GPS-R 規則。

### Phase E：篩選器（尚未完成，需排入 v0.3.x）

**目標:** 快速找到不尋常軌跡。

任務：
1. 新增時間篩選。
2. 新增高度/速度/規則碼篩選。
3. 新增事件列表與地圖同步。
4. 新增「只看異常」切換。
5. 新增「只看 GPS 高度 N 米以下」與「只看滯留 N 分鐘以上」。
6. 新增「只看擄鴿/攔鳥網 GPS-R08/R09/R10」與「只看 A/B 舍 GPS-R05」。

驗收：
- 可只顯示 alt < 90m 且 speed > 72km/h 的 segments。
- 可只顯示 GPS-R08 擄鴿起點。
- 可用 30 分鐘滯留篩選擄鴿嫌疑；用 10 分鐘內短停後轉運篩選 A/B 舍嫌疑。

---

## 5. Commit 與版本規則

使用者要求：commit message 一律包含版本號，方便未來管理。

格式：

```bash
git commit -m "feat(v0.3.0): 新增連續路廊防弊判定"
git commit -m "fix(v0.3.1): 修復高度時間窗 hover 對齊"
git commit -m "docs(v0.3.0): 新增 GPS 防弊系統 blueprint"
```

版本策略：
- patch：bug fix、文案、驗證修正。
- minor：新功能、UI flow、判定規則新增。
- major：資料結構或 API 破壞性變更。

---

## 6. 我的判斷

這些需求是正確方向。GPS 鴿環產品不該只是「畫軌跡」，而是要回答會長真正關心的問題：

1. 哪一段不合理？
2. 不合理的證據是否連續、夠長、可解釋？
3. 疑似被擄鴿/裝車/轉運的第一個點在哪？
4. 是否能快速派人去處理熱點？
5. 是否能用高度、時間、速度、路廊距離交叉驗證？

因此 v0.3.0 不應再追求單純增加 demo 特效，而應升級為「事件導向防弊分析平台」。

---

## 7. 下一步建議

建議下一個實作版本：

```text
v0.3.0：連續路廊與擄鴿起點防弊版
```

優先順序：
1. 連續 20km corridor run 判定（已部分落地，20km 必須保留為可調閥值）。
2. poaching_mountain_net 模擬資料（已部分落地，需確認地圖上可明顯看出山區攔鳥網/轉運起點）。
3. 擄鴿/轉運起點圖示（已部分落地，需補事件點 zoom/定位）。
4. 線條與點位 mouseover 中文化（v0.3.0 立即修正）。
5. 高度/速度 timeline 小窗（尚未完成，下一階段 UI 重點）。
6. 規則 Help/閥值設定（已部分落地，需擴充 Help modal）。
7. 篩選器（尚未完成，需支援日期、高度、速度、規則碼、滯留時間）。
8. 韌體 init/caring/start 狀態機與 ESPConnect `.bin` OTA 文件（先文件化，再進入韌體樣板）。
