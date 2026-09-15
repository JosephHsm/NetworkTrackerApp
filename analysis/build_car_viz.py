"""
차량 세션 시각화 데이터 생성 → viz_build/car_report.html

템플릿(viz_build/car_report.template.html)의 /*__DATA__*/ 자리에 세션 데이터를 JSON으로 넣는다.
브라우저에서 파일을 바로 열어도(file://) 동작하도록 데이터는 HTML 안에 포함한다.

사용법:
    python analysis/build_car_viz.py
"""
import json, os
import numpy as np
import pandas as pd

TEMPLATE = "viz_build/car_report.template.html"
OUT = "viz_build/car_report.html"

# (파일, 표시 이름, 기록상 activity와 다른 경우 메모)
SESSIONS = [
    ("trackingcsv/legacy/network_log_20260524_123419_car.csv", "5/24 차량", ""),
    ("trackingcsv/legacy/network_log_20260607_161233_car.csv", "6/7 차량", ""),
    ("trackingcsv/legacy/network_log_20260609_164800_car.csv", "6/9 차량", ""),
    ("trackingcsv/new/network_log_20260804_222034_unknown.csv", "8/4 차량 추정", "activity=unknown, 속도·이동거리로 차량 판단"),
    ("trackingcsv/new/network_log_20260816_122412_unknown.csv", "8/16 차량 추정", "activity=unknown, 속도·이동거리로 차량 판단"),
    ("trackingcsv/new/network_log_20260914_165631_car.csv", "9/14 차량", ""),
]

MAP_MAX_ACC_M = 50       # 지도에 찍을 GPS 정확도 상한
PINGPONG_S = 30          # 30초 안에 직전 셀로 돌아오면 핑퐁


def col(df, *names):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series(np.nan, index=df.index)


def as_bool(s):
    return s.astype(str).str.lower().eq("true")


def best_neighbor_from_json(row_json, serving_pci):
    try:
        arr = json.loads(row_json)
    except Exception:
        return np.nan
    vals = [a.get("rsrp") for a in arr
            if isinstance(a, dict) and a.get("rsrp") is not None and a.get("pci") != serving_pci]
    return max(vals) if vals else np.nan


def load(path, label, note, idx):
    df = pd.read_csv(path, low_memory=False).sort_values("timestamp", kind="stable").reset_index(drop=True)
    t0 = int(df["timestamp"].iloc[0])
    t = (df["timestamp"] - t0) / 1000.0

    cell = pd.to_numeric(df["serving_cell_id"], errors="coerce")
    rsrp = pd.to_numeric(df["rsrp_dbm"], errors="coerce")

    # 핸드오버: 앱이 기록한 플래그, 없으면(구버전) 서빙셀 ID 변경으로 판정
    if "handover_detected" in df.columns:
        ho = as_bool(df["handover_detected"])
    else:
        prev_cell = cell.ffill().shift()
        ho = cell.notna() & prev_cell.notna() & (cell != prev_cell)

    # 핸드오버 직전 셀과 그 RSRP
    last_cell = cell.where(~ho).ffill().shift()          # 직전 행까지의 서빙셀
    prev_cell_col = pd.to_numeric(col(df, "prev_serving_cell_id"), errors="coerce").fillna(last_cell)
    prev_rsrp = pd.to_numeric(col(df, "prev_rsrp_dbm"), errors="coerce").fillna(rsrp.shift())

    if "ping_pong_detected" in df.columns:
        pp = as_bool(df["ping_pong_detected"])
    else:
        pp = pd.Series(False, index=df.index)
        hist = []  # (cell, 떠난 시각)
        for i in np.where(ho)[0]:
            hist = [(c, s) for c, s in hist if t[i] - s < PINGPONG_S]
            pp[i] = any(c == cell[i] for c, _ in hist)
            hist.append((prev_cell_col[i], t[i]))

    if "best_nbr_rsrp_dbm" in df.columns:
        nbr = pd.to_numeric(df["best_nbr_rsrp_dbm"], errors="coerce")
    else:
        nbr = pd.Series([best_neighbor_from_json(j, p) for j, p in zip(df["neighbors_json"], df["serving_pci"])])

    acc = pd.to_numeric(df["gps_accuracy_m"], errors="coerce")
    stale = col(df, "location_source").astype(str).str.startswith("stale")
    map_ok = df["latitude"].notna() & (acc <= MAP_MAX_ACC_M) & ~stale

    rx = pd.to_numeric(col(df, "mobile_rx_bitrate_Mbps", "rx_bitrate_Mbps"), errors="coerce")
    tx = pd.to_numeric(col(df, "mobile_tx_bitrate_Mbps", "tx_bitrate_Mbps"), errors="coerce")
    speed = pd.to_numeric(df["gps_speed_ms"], errors="coerce")

    def r(v, nd):
        return None if pd.isna(v) else round(float(v), nd)

    pts = []
    for i in range(len(df)):
        c = cell[i]
        pts.append([
            round(float(t[i]), 1),
            r(df["latitude"][i], 6) if map_ok[i] else None,
            r(df["longitude"][i], 6) if map_ok[i] else None,
            None if pd.isna(c) else int(c),
            r(rsrp[i], 0),
            r(col(df, "sinr_snr_db")[i], 1),
            r(col(df, "rsrq_db")[i], 0),
            r(nbr[i], 0),
            r(speed[i], 1),
            r(rx[i], 3),
            r(tx[i], 3),
            r(col(df, "serving_pci")[i], 0),
            r(col(df, "serving_freq_arfcn")[i], 0),
            1 if ho[i] else 0,
            1 if pp[i] else 0,
        ])

    events = []
    for i in np.where(ho)[0]:
        fc = prev_cell_col[i]
        tc = cell[i]
        if pd.isna(fc) or pd.isna(tc):
            continue
        events.append([
            round(float(t[i]), 1), int(fc), int(tc),
            1 if int(fc) // 256 == int(tc) // 256 else 0,
            r(prev_rsrp[i], 0), r(rsrp[i], 0), r(speed[i], 1), 1 if pp[i] else 0,
        ])

    dur_min = float(t.iloc[-1]) / 60
    lat, lon = df["latitude"].where(map_ok), df["longitude"].where(map_ok)
    km = float(np.nansum(np.hypot(np.diff(lat) * 111000, np.diff(lon) * 88000))) / 1000
    return dict(
        id=idx, label=label, note=note, file=os.path.basename(path), t0=t0,
        dur_min=round(dur_min, 1), km=round(km, 1), rows=len(df),
        has_sinr=bool(col(df, "sinr_snr_db").notna().mean() > 0.5),
        pts=pts, events=events,
    )


def main():
    sessions = [load(p, lab, note, i) for i, (p, lab, note) in enumerate(SESSIONS)]
    data = dict(
        cols=["t", "lat", "lon", "cell", "rsrp", "sinr", "rsrq", "nbr", "speed", "rx", "tx", "pci", "arfcn", "ho", "pp"],
        ev_cols=["t", "from", "to", "same_enb", "rsrp_prev", "rsrp_new", "speed", "pp"],
        sessions=sessions,
    )
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = html.replace("/*__DATA__*/null", payload)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    for s in sessions:
        print(f"{s['label']:<12} {s['rows']:5d}행 {s['dur_min']:5.1f}분 {s['km']:5.1f}km 핸드오버 {len(s['events'])}건")
    print(f"→ {OUT} ({os.path.getsize(OUT) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
