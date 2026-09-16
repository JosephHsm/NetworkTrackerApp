"""
세션 시각화 데이터 생성 → viz_build/report_{all,car,subway}.html

템플릿(viz_build/report.template.html)의 /*__DATA__*/ 자리에 세션 데이터를 JSON으로 넣는다.
브라우저에서 파일을 바로 열어도(file://) 동작하도록 데이터는 HTML 안에 포함한다.

대상은 trackingcsv/new 6개 세션이고, 통합·차량·지하철 세 가지 리포트를 만든다.

원본 CSV는 절대 수정하지 않는다. 자르기·동결 제외는 모두 이 스크립트 안에서만 일어나고,
얼마나 잘랐는지는 리포트에 그대로 표시된다.

사용법:
    python analysis/build_viz.py
"""
import json, os
import numpy as np
import pandas as pd

TEMPLATE = "viz_build/report.template.html"
MAPMATCH_DIR = "analysis_output"
OUTS = {
    "all":    ("viz_build/report_all.html",    "전체 세션",   "이동하는 동안 폰은 어디에 붙고, 무엇이 속도를 정하는가"),
    "car":    ("viz_build/report_car.html",    "차량 세션",   "차로 달리는 동안 폰은 어디에 붙고, 무엇이 속도를 정하는가"),
    "subway": ("viz_build/report_subway.html", "지하철 세션", "지하철을 타는 동안 폰은 어디에 붙고, 무엇이 속도를 정하는가"),
}

MAP_MAX_ACC_M = 50       # 지도에 찍을 GPS 정확도 상한
PINGPONG_S = 30          # 30초 안에 직전 셀로 돌아오면 핑퐁
BIN_S = 30 * 60          # 구간 길이 — 30분
# 이보다 짧은 구간은 30분 환산이 과도한 외삽이라 신뢰도를 낮춰 표시한다.
# 5분으로 잡은 이유: 9.9분/49건(8/4)·8.7분/31건(9/9)은 이벤트가 충분해 비율 추정이 멀쩡한 반면,
# 2.2분/3건·1.6분/11건짜리 자투리는 30분으로 늘리면 값이 두 배 이상 튄다.
SHORT_BIN_S = 5 * 60
FROZEN_RUN = 3           # 같은 좌표가 이만큼 연속되면 GPS 동결로 본다

# keep: (시작분, 끝분) — 수집을 늦게 시작하거나 끄는 걸 깜빡한 구간을 시각화에서만 제외한다.
#       근거는 analysis 단계에서 분 단위 이동거리·서빙셀 수로 확인했고 trim_reason에 남긴다.
SESSIONS = [
    dict(path="trackingcsv/new/network_log_20260804_222034_unknown.csv",
         label="8/4 차량", activity="car",
         note="activity=unknown, 속도·이동거리로 차량 판단",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260816_122412_unknown.csv",
         label="8/16 차량", activity="car",
         note="activity=unknown, 속도·이동거리로 차량 판단",
         keep=(0, 58),
         trim_reason="58분 이후 35분간 이동 0~4 m·서빙셀 1개로 멈춰 있어 제외 (수집 종료를 깜빡한 구간)"),
    dict(path="trackingcsv/new/network_log_20260909_165646_subway.csv",
         label="9/9 지하철", activity="subway", note="", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260914_165631_car.csv",
         label="9/14 차량", activity="car", note="", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260915_165206_subway.csv",
         label="9/15 지하철", activity="subway",
         note="측정 중 activity 태그 누락 — 데이터로 지하철 판별",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260915_200513_car.csv",
         label="9/15 차량", activity="car", keep=(1, 17),
         note="",
         trim_reason="앞 1분은 출발 전 대기(이동 68 m·0.1 m/s), 17분 이후는 도착 후 미종료(이동 5·4·2 m)"),
]

# 앱 버전별 기록 항목. 컬럼 수로 판별한다.
VERSIONS = {
    61: dict(
        name="v1.0",
        summary="기본 61개 항목. 신호·셀·트래픽·GPS는 있지만 지하 측위 보강과 능동 측정이 아직 없다.",
        missing=["rtt_ms / probe_dl_mbps (능동 측정)", "pressure_hpa (기압)",
                 "location_source / location_age_s (위치 출처·나이)",
                 "anchor_station (역 태그)", "wifi_scan_json (Wi-Fi 지문)",
                 "prev_neighbors_json (직전 이웃셀)"],
    ),
    73: dict(
        name="v1.1",
        summary="61개에 12개가 더해졌다. 지하 측위 보강(기압·위치 출처·역 태그·Wi-Fi 지문)과 "
                "능동 측정 프로브(지연 시간·다운로드)가 들어왔다.",
        missing=[],
    ),
}
V11_ADDED = ["rtt_ms", "probe_dl_mbps", "pressure_hpa", "location_source", "location_age_s",
             "anchor_station", "anchor_lat", "anchor_lon", "wifi_ap_count", "wifi_scan_age_s",
             "wifi_scan_json", "prev_neighbors_json"]


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


def frozen_mask(lat, lon):
    """같은 좌표가 FROZEN_RUN행 이상 연속되면 GPS 동결로 본다.

    8/4 세션은 좌표가 얼어붙은 동안에도 gps_speed가 10 m/s를 가리킨다 — 멈춘 게 아니라
    위치가 갱신되지 않은 것이므로, 정지로 오인하지 않도록 '지도에 쓰지 않음'으로만 처리한다.
    """
    key = lat.astype(str) + "," + lon.astype(str)
    grp = (key != key.shift()).cumsum()
    size = grp.map(grp.value_counts())
    return (size >= FROZEN_RUN).to_numpy()


def load_frame(s):
    """원본 CSV(지하철은 맵매칭 결과)를 읽고 keep 구간만 남긴다."""
    name = os.path.splitext(os.path.basename(s["path"]))[0]
    mm = os.path.join(MAPMATCH_DIR, name + "_mapmatched.csv")
    use_mm = s["activity"] == "subway" and os.path.exists(mm)
    df = pd.read_csv(mm if use_mm else s["path"], low_memory=False)
    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)

    raw_min = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]) / 60000.0
    t0 = int(df["timestamp"].iloc[0])
    cut_head = cut_tail = 0.0
    if s["keep"]:
        a, b = s["keep"]
        rel = (df["timestamp"] - t0) / 60000.0
        keep = (rel >= a) & (rel <= b)
        cut_head, cut_tail = a, max(0.0, raw_min - b)
        df = df[keep].reset_index(drop=True)
    return df, use_mm, raw_min, cut_head, cut_tail


def make_bins(t, ho, pp, rsrp, rx, cell):
    """세션을 30분 구간으로 나눈 지표. 30분이 안 되면 나누지 않고 한 구간."""
    out = []
    total = float(t.iloc[-1])
    n = max(1, int(np.ceil(total / BIN_S)))
    for k in range(n):
        m = (t >= k * BIN_S) & (t < (k + 1) * BIN_S) if k < n - 1 else (t >= k * BIN_S)
        if not m.any():
            continue
        tt = t[m]
        span = float(tt.max() - tt.min())
        rxv = rx[m].dropna()
        rxv = rxv[rxv >= 0.1]
        out.append(dict(
            i=k + 1,
            start=round(k * BIN_S / 60.0, 1),
            span=round(span / 60.0, 1),
            short=bool(span < SHORT_BIN_S),
            rows=int(m.sum()),
            ho=int(ho[m].sum()),
            pp=int(pp[m].sum()),
            ho30=round(float(ho[m].sum()) / max(span, 1e-9) * BIN_S, 1),
            rsrp=None if rsrp[m].dropna().empty else round(float(rsrp[m].median()), 1),
            rx=None if rxv.empty else round(float(rxv.median()), 3),
            cells=int(pd.to_numeric(cell[m], errors="coerce").nunique()),
        ))
    return out


def load(s, idx):
    df, use_mm, raw_min, cut_head, cut_tail = load_frame(s)
    t0 = int(df["timestamp"].iloc[0])
    t = (df["timestamp"] - t0) / 1000.0

    cell = pd.to_numeric(df["serving_cell_id"], errors="coerce")
    rsrp = pd.to_numeric(df["rsrp_dbm"], errors="coerce")

    if "handover_detected" in df.columns:
        ho = as_bool(df["handover_detected"])
    else:
        prev_cell = cell.ffill().shift()
        ho = cell.notna() & prev_cell.notna() & (cell != prev_cell)

    last_cell = cell.where(~ho).ffill().shift()
    prev_cell_col = pd.to_numeric(col(df, "prev_serving_cell_id"), errors="coerce").fillna(last_cell)
    prev_rsrp = pd.to_numeric(col(df, "prev_rsrp_dbm"), errors="coerce").fillna(rsrp.shift())

    if "ping_pong_detected" in df.columns:
        pp = as_bool(df["ping_pong_detected"])
    else:
        pp = pd.Series(False, index=df.index)
        hist = []
        for i in np.where(ho)[0]:
            hist = [(c, sec) for c, sec in hist if t[i] - sec < PINGPONG_S]
            pp[i] = any(c == cell[i] for c, _ in hist)
            hist.append((prev_cell_col[i], t[i]))

    if "best_nbr_rsrp_dbm" in df.columns:
        nbr = pd.to_numeric(df["best_nbr_rsrp_dbm"], errors="coerce")
    else:
        nbr = pd.Series([best_neighbor_from_json(j, p)
                         for j, p in zip(df["neighbors_json"], df["serving_pci"])])

    lat_raw = pd.to_numeric(df["latitude"], errors="coerce")
    lon_raw = pd.to_numeric(df["longitude"], errors="coerce")
    frozen = frozen_mask(lat_raw, lon_raw)

    if use_mm:
        # 지하철: 역 보정 좌표를 쓴다. 땅속 GPS는 얼어 있거나 기지국 기반이라 못 쓴다.
        lat = pd.to_numeric(df["lat_mm"], errors="coerce")
        lon = pd.to_numeric(df["lon_mm"], errors="coerce")
        map_ok = lat.notna() & lon.notna()
        coord_src = "역 보정 (맵매칭)"
    else:
        acc = pd.to_numeric(df["gps_accuracy_m"], errors="coerce")
        stale = col(df, "location_source").astype(str).str.startswith("stale")
        lat, lon = lat_raw, lon_raw
        map_ok = lat.notna() & (acc <= MAP_MAX_ACC_M) & ~stale & ~frozen
        coord_src = "GPS"

    rx = pd.to_numeric(col(df, "mobile_rx_bitrate_Mbps", "rx_bitrate_Mbps"), errors="coerce")
    tx = pd.to_numeric(col(df, "mobile_tx_bitrate_Mbps", "tx_bitrate_Mbps"), errors="coerce")
    speed = pd.to_numeric(df["gps_speed_ms"], errors="coerce")
    # 좌표가 얼어 있는 동안의 속도는 직전 값이 그대로 남은 것이라 속도 그림에서 뺀다
    speed = speed.where(~frozen)

    def r(v, nd):
        return None if pd.isna(v) else round(float(v), nd)

    pts = []
    for i in range(len(df)):
        c = cell[i]
        pts.append([
            round(float(t[i]), 1),
            r(lat[i], 6) if map_ok[i] else None,
            r(lon[i], 6) if map_ok[i] else None,
            None if pd.isna(c) else int(c),
            r(rsrp[i], 0), r(col(df, "sinr_snr_db")[i], 1), r(col(df, "rsrq_db")[i], 0),
            r(nbr[i], 0), r(speed[i], 1), r(rx[i], 3), r(tx[i], 3),
            r(col(df, "serving_pci")[i], 0), r(col(df, "serving_freq_arfcn")[i], 0),
            1 if ho[i] else 0, 1 if pp[i] else 0,
        ])

    events = []
    for i in np.where(ho)[0]:
        fc, tc = prev_cell_col[i], cell[i]
        if pd.isna(fc) or pd.isna(tc):
            continue
        events.append([
            round(float(t[i]), 1), int(fc), int(tc),
            1 if int(fc) // 256 == int(tc) // 256 else 0,
            r(prev_rsrp[i], 0), r(rsrp[i], 0), r(speed[i], 1), 1 if pp[i] else 0,
        ])

    dur_min = float(t.iloc[-1]) / 60
    la, lo = lat.where(map_ok), lon.where(map_ok)
    km = float(np.nansum(np.hypot(np.diff(la) * 111000, np.diff(lo) * 88000))) / 1000

    ncols = len(pd.read_csv(s["path"], nrows=0).columns)
    ver = VERSIONS.get(ncols, VERSIONS[73])

    return dict(
        id=idx, label=s["label"], note=s["note"], file=os.path.basename(s["path"]), t0=t0,
        activity=s["activity"], activity_ko="차량" if s["activity"] == "car" else "지하철",
        dur_min=round(dur_min, 1), raw_min=round(raw_min, 1), km=round(km, 1), rows=len(df),
        cut_head=round(cut_head, 1), cut_tail=round(cut_tail, 1), trim_reason=s["trim_reason"],
        version=ver["name"], ncols=ncols, coord_src=coord_src,
        frozen_share=round(100.0 * frozen.mean(), 1),
        has_sinr=bool(col(df, "sinr_snr_db").notna().mean() > 0.5),
        bins=make_bins(t, ho, pp, rsrp, rx, cell),
        pts=pts, events=events,
    )


def build(scope, sessions, html):
    out, eyebrow, title = OUTS[scope]
    picked = [s for s in sessions if scope == "all" or s["activity"] == scope]
    # 리포트마다 id를 0부터 다시 매긴다 (템플릿이 SESS[id]로 참조한다)
    picked = [dict(s, id=i) for i, s in enumerate(picked)]
    used = sorted({s["ncols"] for s in picked})
    data = dict(
        cols=["t", "lat", "lon", "cell", "rsrp", "sinr", "rsrq", "nbr", "speed", "rx", "tx",
              "pci", "arfcn", "ho", "pp"],
        ev_cols=["t", "from", "to", "same_enb", "rsrp_prev", "rsrp_new", "speed", "pp"],
        meta=dict(scope=scope, eyebrow="NetworkTrackerApp · " + eyebrow, title=title,
                  kind={"all": "차량과 지하철로 이동하며", "car": "차량으로 이동하며",
                        "subway": "지하철로 이동하며"}[scope]),
        versions=[dict(VERSIONS[c], cols=c,
                       added=V11_ADDED if c == 73 else [],
                       files=[s["label"] for s in picked if s["ncols"] == c]) for c in used],
        sessions=picked,
    )
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(html.replace("/*__DATA__*/null", payload))
    print(f"  → {out} ({os.path.getsize(out) / 1024:.0f} KB, 세션 {len(picked)}개)")


def main():
    sessions = [load(s, i) for i, s in enumerate(SESSIONS)]
    for s in sessions:
        cut = ""
        if s["cut_head"] or s["cut_tail"]:
            cut = f"  [자름 앞 {s['cut_head']}분 / 뒤 {s['cut_tail']}분, 원본 {s['raw_min']}분]"
        print(f"{s['label']:<12} {s['activity_ko']:<4} {s['version']} {s['rows']:5d}행 "
              f"{s['dur_min']:5.1f}분 {s['km']:5.1f}km 핸드오버 {len(s['events']):3d}건 "
              f"구간 {len(s['bins'])}개 동결 {s['frozen_share']:4.1f}%{cut}")
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    for scope in OUTS:
        build(scope, sessions, html)


if __name__ == "__main__":
    main()
