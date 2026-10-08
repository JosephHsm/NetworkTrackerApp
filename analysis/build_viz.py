"""
세션 시각화 데이터 생성 → viz_build/report_{all,car,subway}.html

템플릿(viz_build/report.template.html)의 /*__DATA__*/ 자리에 세션 데이터를 JSON으로 넣는다.
브라우저에서 파일을 바로 열어도(file://) 동작하도록 데이터는 HTML 안에 포함한다.

대상은 trackingcsv/new 세션 전부이고, 통합·차량·지하철·도보 네 가지 리포트를 만든다.

원본 CSV는 절대 수정하지 않는다. 자르기·동결 제외는 모두 이 스크립트 안에서만 일어나고,
얼마나 잘랐는지는 리포트에 그대로 표시된다.

사용법:
    python analysis/build_viz.py
"""
import json, os
import numpy as np
import pandas as pd

import cell_stats
import neighbor_eci

TEMPLATE = "viz_build/report.template.html"
INDEX_TEMPLATE = "viz_build/index.template.html"   # 교수님용 첫 화면 (리포트·CSV·세션 목록 모음)
INDEX_OUT = "viz_build/index.html"
MAPMATCH_DIR = "analysis_output"
SECTOR_JSON = "analysis_output/stats/sector_rules.json"
NBR_JSON = "analysis_output/stats/neighbor_eci.json"
OUTS = {
    "all":     ("viz_build/report_all.html",     "전체 세션",   "이동하는 동안 폰은 어디에 붙고, 무엇이 속도를 정하는가"),
    "car":     ("viz_build/report_car.html",     "차량 세션",   "차로 달리는 동안 폰은 어디에 붙고, 무엇이 속도를 정하는가"),
    "subway":  ("viz_build/report_subway.html",  "지하철 세션", "지하철을 타는 동안 폰은 어디에 붙고, 무엇이 속도를 정하는가"),
    "walking": ("viz_build/report_walking.html", "도보 세션",   "걸어가는 동안 폰은 어디에 붙고, 무엇이 속도를 정하는가"),
}

SEG_COLS = ["key", "enb", "t_start", "dwell_s", "censored", "n_cells", "n_intra_ho",
            "straight_m", "path_m", "speed_mean", "rsrp_mean", "nr_share", "end_type", "end_pp"]

ACTIVITY_KO = {"car": "차량", "subway": "지하철", "walking": "도보", "home": "정지"}

MAP_MAX_ACC_M = 50       # 지도에 찍을 GPS 정확도 상한
PINGPONG_S = 30          # 30초 안에 직전 셀로 돌아오면 핑퐁
BIN_S = 30 * 60          # 구간 길이 — 30분
# 이보다 짧은 구간은 30분 환산이 과도한 외삽이라 신뢰도를 낮춰 표시한다.
# 5분으로 잡은 이유: 9.9분/49건(8/4)·8.7분/31건(9/9)은 이벤트가 충분해 비율 추정이 멀쩡한 반면,
# 2.2분/3건·1.6분/11건짜리 자투리는 30분으로 늘리면 값이 두 배 이상 튄다.
SHORT_BIN_S = 5 * 60
FROZEN_RUN = 3           # 같은 좌표가 이만큼 연속되면 GPS 동결로 본다
# 직전 행과 이보다 가까운 행은 송수신 속도를 버린다. 핸드오버 행이 정기 수집 사이에 끼면
# 바로 다음 정기 행의 측정 창이 1초 남짓이 되는데, 그 창에서는 OS 트래픽 카운터가 아직 안 올라
# 48%가 0으로 찍혔다(v1.2 기준, 창 2초 이상은 0이 하나도 없음). 망이 멈춘 게 아니라 측정 착시다.
MIN_RATE_WINDOW_S = 1.5

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
    dict(path="trackingcsv/new/network_log_20260920_123732_subway.csv",
         label="9/20 지하철", activity="subway",
         note="activity 태그 누락 — 2호선 건대입구→을지로3가, 3호선 환승 후 안국. 역 태그 3개에 사후 앵커 8개를 보탰다",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260921_141259_subway.csv",
         label="9/21 지하철", activity="subway",
         note="activity 태그 누락 — 2호선 동대문역사문화공원→건대입구. 역 태그 2개에 사후 앵커 5개를 보탰다",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260921_142715_walking.csv",
         label="9/21 도보", activity="walking",
         note="지하철에서 내린 뒤 이어서 걸은 구간 (1.1 m/s 일정)",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260924_140453_subway.csv",
         label="9/24 지하철", activity="subway",
         note="activity 태그 누락 — 7호선 건대입구→상동. 역 태그 2개에 사후 앵커 16개를 보탰다",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260925_072530_car.csv",
         label="9/25 07:25 차량", activity="car",
         note="activity 태그 누락 — 역 태그 없음·속도 중앙값 15 m/s로 차량 판단", keep=(1, 26),
         trim_reason="앞 1분은 출발 전 대기(이동 3 m·0 m/s)"),
    dict(path="trackingcsv/new/network_log_20260925_154924_car.csv",
         label="9/25 15:49 차량", activity="car",
         note="activity 태그 누락 — 역 태그 없음·최고 20 m/s로 차량 판단(정체 구간이 길다)",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260925_163819_car.csv",
         label="9/25 16:38 차량", activity="car",
         note="activity 태그 누락 — 역 태그 없음·최고 24 m/s로 차량 판단. 15:49 세션에서 이어진다",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260927_205144_car.csv",
         label="9/27 차량", activity="car",
         note="수집 종료를 깜빡해 원본이 21:58까지 이어졌다 — 21:33 주차까지만 이 파일로 잘랐다",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260927_213901_home.csv",
         label="9/27 정지", activity="home",
         note="9/27 차량 원본의 도착 후 구간(21:39~21:58)을 잘라낸 실내 정지 측정. 주차~입실 5분은 양쪽에서 뺐다",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260928_171804_subway.csv",
         label="9/28 17:18 지하철", activity="subway",
         note="2호선 건대입구→시청. 역 태그 5개에 사후 앵커 7개를 보탰다", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260928_174322_subway.csv",
         label="9/28 17:43 지하철", activity="subway",
         note="1호선 시청→용산", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260928_195736_subway.csv",
         label="9/28 19:57 지하철", activity="subway",
         note="2호선 시청→건대입구. 역 태그 3개에 사후 앵커 8개를 보탰다", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260929_135542_car.csv",
         label="9/29 13:55 차량", activity="car", note="", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260929_143147_car.csv",
         label="9/29 14:31 차량", activity="car", note="", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260929_183716_car.csv",
         label="9/29 18:37 차량", activity="car", note="", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20260929_191910_car.csv",
         label="9/29 19:19 차량", activity="car", note="", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20261001_133919_car.csv",
         label="10/1 13:39 차량", activity="car", note="", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20261001_143101_car.csv",
         label="10/1 14:31 차량", activity="car", note="", keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20261002_150542_walking.csv",
         label="10/2 도보", activity="walking",
         note="activity 태그 누락 — 1.1 m/s 일정. 수집 종료를 깜빡해 원본이 15:25까지 이어졌다 — 15:19 도착까지만 이 파일로 잘랐다",
         keep=None, trim_reason=""),
    dict(path="trackingcsv/new/network_log_20261004_141602_walking.csv",
         label="10/4 도보", activity="walking",
         note="activity 태그 누락 — 1.1 m/s 일정. 원본 끝 2분 40초는 멈춰 선 채 종료를 누르지 않은 구간이라 잘랐다",
         keep=None, trim_reason=""),
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
        missing=["probe_ul_mbps (업로드 실측)"],
    ),
    74: dict(
        name="v1.2",
        summary="능동 프로브가 30초 단위 버스트(2MB/30초)에서 지속 부하로 바뀌었다(다운·업 각각 초당 0.06MB = 0.48 Mbps). "
                "업로드는 실제로 고르게 나가 송신 속도가 96%의 순간에 0.4 Mbps 이상으로 찍힌다. "
                "다운로드는 앱이 천천히 읽어도 OS 수신 버퍼가 먼저 몰아 받아, 망에서는 여전히 버스트로 오간다 — "
                "그래서 probe_dl_mbps는 버퍼에서 읽은 속도라 망 상태와 무관하게 목표치 근처에 머문다(분석에 쓰지 않는다).",
        missing=["speedtest_* (속도 측정)"],
    ),
    79: dict(
        name="v1.3",
        summary="속도 측정(Speed Test)이 들어왔다. 정해진 간격(30초~2분)마다 3초 동안 페이싱 없이 다운로드해 "
                "'그 순간 그 셀에서 받을 수 있는 최대 속도'를 잰다(첫 0.5초는 TCP 느린 시작이라 뺀다). "
                "측정 직전(speedtest_start)과 직후(speedtest)에 행이 하나씩 더 남아, 같은 몇 초의 RSRP·SINR과 속도를 짝지을 수 있다. "
                "셀이 바뀔 때마다 새 셀에서도 바로 잰다(speedtest_reason = handover, 직전 측정 10초 안이면 건너뜀).",
        missing=[],
    ),
}
ADDED = {
    73: ["rtt_ms", "probe_dl_mbps", "pressure_hpa", "location_source", "location_age_s",
         "anchor_station", "anchor_lat", "anchor_lon", "wifi_ap_count", "wifi_scan_age_s",
         "wifi_scan_json", "prev_neighbors_json"],
    74: ["probe_ul_mbps"],
    79: ["speedtest_dl_mbps", "speedtest_bytes", "speedtest_ms", "speedtest_ttfb_ms", "speedtest_reason"],
}


def col(df, *names):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series(np.nan, index=df.index)


def as_bool(s):
    return s.astype(str).str.lower().eq("true")


def best_neighbor(row_json, serving_pci, serving_arfcn, same_freq):
    """이웃 셀 중 가장 센 RSRP.

    same_freq=True면 서빙과 같은 주파수(ARFCN)만 본다.
    앱이 기록하는 best_nbr_rsrp_dbm은 주파수를 가리지 않는데, 그렇게 고른 최강 이웃의 86%가
    서빙과 다른 밴드였다. 밴드가 다르면 경로손실도 출력도 달라 RSRP를 직접 비교할 수 없고,
    그대로 쓰면 "서빙이 최강인 경우가 26%뿐"이라는 잘못된 결론이 나온다
    (주파수를 맞추면 90%). 핸드오버 판정(A3)도 같은 주파수 안에서 이뤄지므로 그림 3은 이 값을 쓴다.
    """
    try:
        arr = json.loads(row_json)
    except Exception:
        return np.nan
    if same_freq and pd.isna(serving_arfcn):
        return np.nan
    vals = []
    for a in arr:
        if not isinstance(a, dict) or a.get("rsrp") is None:
            continue
        ea = a.get("earfcn")
        if a.get("pci") == serving_pci and ea == serving_arfcn:
            continue                      # 서빙 자신
        if same_freq and ea != serving_arfcn:
            continue
        vals.append(a["rsrp"])
    return max(vals) if vals else np.nan


def target_rank(prev_json, new_pci, new_arfcn):
    """핸드오버 직전에 보이던 이웃 중, 옮겨간 셀이 몇 번째로 셌는지.

    같은 주파수(ARFCN)끼리만 줄을 세운다 — 다른 밴드는 세기를 맞대어 비교할 대상이 아니다.
    반환: (순위 1부터, 같은 주파수 이웃 수, 1위 대비 손해 dB). 목록에 없으면 순위·손해가 None.
    """
    if pd.isna(new_arfcn) or pd.isna(new_pci):
        return None, 0, None
    try:
        arr = json.loads(prev_json)
    except Exception:
        return None, 0, None
    same = [(a.get("pci"), a.get("rsrp")) for a in arr
            if isinstance(a, dict) and a.get("rsrp") is not None and a.get("earfcn") == new_arfcn]
    same.sort(key=lambda x: -x[1])
    if not same:
        return None, 0, None
    for k, (pci, v) in enumerate(same):
        if pci == new_pci:
            return k + 1, len(same), same[0][1] - v
    return None, len(same), None


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


def session_coords(df, use_mm):
    """지도·거리 계산에 쓰는 좌표 → (lat, lon, map_ok, frozen, coord_src). 이웃 셀 추정도 같은 좌표를 쓴다."""
    lat_raw = pd.to_numeric(df["latitude"], errors="coerce")
    lon_raw = pd.to_numeric(df["longitude"], errors="coerce")
    frozen = frozen_mask(lat_raw, lon_raw)
    if use_mm:
        # 지하철: 역 보정 좌표를 쓴다. 땅속 GPS는 얼어 있거나 기지국 기반이라 못 쓴다.
        lat = pd.to_numeric(df["lat_mm"], errors="coerce")
        lon = pd.to_numeric(df["lon_mm"], errors="coerce")
        return lat, lon, lat.notna() & lon.notna(), frozen, "역 보정 (맵매칭)"
    acc = pd.to_numeric(df["gps_accuracy_m"], errors="coerce")
    stale = col(df, "location_source").astype(str).str.startswith("stale")
    return lat_raw, lon_raw, lat_raw.notna() & (acc <= MAP_MAX_ACC_M) & ~stale & ~frozen, frozen, "GPS"


def build_nbr_index():
    """전 세션의 접속 기록(셀 ID·PCI·EARFCN·위치)으로 이웃 셀 ID 추정 색인을 만든다."""
    obs = []
    for s in SESSIONS:
        df, use_mm, *_ = load_frame(s)
        lat, lon, ok, _, _ = session_coords(df, use_mm)
        obs += zip([os.path.basename(s["path"])] * len(df), pd.to_numeric(df["serving_cell_id"], errors="coerce"),
                   pd.to_numeric(col(df, "serving_pci"), errors="coerce"),
                   pd.to_numeric(col(df, "serving_freq_arfcn"), errors="coerce"), lat.where(ok), lon.where(ok))
    return neighbor_eci.Index(obs)


def load(s, idx, nbr_index=None):
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

    # nbr  : 같은 주파수 최강 이웃 (핸드오버 비교에 쓰는 값)
    # nbrx : 주파수 무관 최강 이웃 (앱의 best_nbr_rsrp_dbm과 같은 정의 — 밴드 간 이동 참고용)
    s_pci = pd.to_numeric(col(df, "serving_pci"), errors="coerce")
    s_arf = pd.to_numeric(col(df, "serving_freq_arfcn"), errors="coerce")
    nbr = pd.Series([best_neighbor(j, p, a, True)
                     for j, p, a in zip(df["neighbors_json"], s_pci, s_arf)])
    nbrx = pd.Series([best_neighbor(j, p, a, False)
                      for j, p, a in zip(df["neighbors_json"], s_pci, s_arf)])

    lat, lon, map_ok, frozen, coord_src = session_coords(df, use_mm)

    # 같은 주파수 최강 이웃의 셀 ID 추정 (폰은 이웃의 셀 ID를 안 준다 — neighbor_eci.py)
    nbr_cell, nbr_conf = [None] * len(df), [0] * len(df)
    if nbr_index is not None:
        for i, (js, p, a) in enumerate(zip(df["neighbors_json"], s_pci, s_arf)):
            b = neighbor_eci.strongest_same_freq(js, p, a) if isinstance(js, str) else None
            res = nbr_index.infer(b[0], b[1], lat[i] if map_ok[i] else np.nan, lon[i] if map_ok[i] else np.nan) if b else None
            if res:
                nbr_cell[i], nbr_conf[i] = res[0], 1 if res[1] == "unique" else 2

    short_win = (df["timestamp"].diff() / 1000.0 <= MIN_RATE_WINDOW_S).to_numpy()
    rx = pd.to_numeric(col(df, "mobile_rx_bitrate_Mbps", "rx_bitrate_Mbps"), errors="coerce").mask(short_win)
    tx = pd.to_numeric(col(df, "mobile_tx_bitrate_Mbps", "tx_bitrate_Mbps"), errors="coerce").mask(short_win)
    rtt = pd.to_numeric(col(df, "rtt_ms"), errors="coerce")
    speed = pd.to_numeric(df["gps_speed_ms"], errors="coerce")
    # 좌표가 얼어 있는 동안의 속도는 직전 값이 그대로 남은 것이라 속도 그림에서 뺀다
    speed = speed.where(~frozen)

    rat = cell_stats.rat_code(col(df, "override_network_type"))

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
            r(nbr[i], 0), r(nbrx[i], 0), r(speed[i], 1), r(rx[i], 3), r(tx[i], 3),
            r(col(df, "serving_pci")[i], 0), r(col(df, "serving_freq_arfcn")[i], 0),
            1 if ho[i] else 0, 1 if pp[i] else 0, int(rat[i]), r(rtt[i], 0),
            nbr_cell[i], nbr_conf[i],
        ])

    # 핸드오버 직전에 보이던 이웃 목록: v1.1은 그 행에 남아 있고(prev_neighbors_json),
    # 없으면 직전 행의 목록을 쓴다.
    prev_nb = col(df, "prev_neighbors_json")
    events = []
    for i in np.where(ho)[0]:
        fc, tc = prev_cell_col[i], cell[i]
        if pd.isna(fc) or pd.isna(tc):
            continue
        pj = prev_nb[i] if isinstance(prev_nb[i], str) and prev_nb[i].strip() not in ("", "[]")             else (df["neighbors_json"][i - 1] if i > 0 else "")
        rk, nsame, loss = target_rank(pj, s_pci[i], s_arf[i])
        events.append([
            round(float(t[i]), 1), int(fc), int(tc),
            1 if int(fc) // 256 == int(tc) // 256 else 0,
            r(prev_rsrp[i], 0), r(rsrp[i], 0), r(speed[i], 1), 1 if pp[i] else 0,
            rk, nsame, None if loss is None else round(float(loss), 1),
        ])

    # 속도 측정(v1.3): "speedtest" 행마다 직전 "speedtest_start" 행(15초 안)과 짝지어
    # 같은 구간의 RSRP·SINR(두 행 평균)과 다운로드 속도를 한 점으로 만든다
    speedtests = []
    if "speedtest_dl_mbps" in df.columns:
        trig = df["collect_trigger"].astype(str).to_numpy()
        stm = pd.to_numeric(df["speedtest_dl_mbps"], errors="coerce")
        streason = col(df, "speedtest_reason").astype(str).to_numpy()
        sinr_all = pd.to_numeric(col(df, "sinr_snr_db"), errors="coerce")
        last_start = None
        for i in range(len(df)):
            if trig[i] == "speedtest_start":
                last_start = i
            elif trig[i] == "speedtest" and pd.notna(stm[i]):
                a = last_start if last_start is not None and t[i] - t[last_start] <= 15 else i
                both = [a, i]
                speedtests.append([
                    round(float(t[i]), 1), round(float(stm[i]), 2),
                    r(rsrp[both].mean(), 1), r(sinr_all[both].mean(), 1), r(speed[i], 1),
                    1 if pd.notna(cell[a]) and pd.notna(cell[i]) and cell[a] != cell[i] else 0, int(rat[i]),
                    1 if streason[i] == "handover" else 0,
                ])
                last_start = None

    dur_min = float(t.iloc[-1]) / 60
    la, lo = lat.where(map_ok), lon.where(map_ok)
    km = float(np.nansum(np.hypot(np.diff(la) * 111000, np.diff(lo) * 88000))) / 1000

    # 셀 유지 구간 — cell_stats.py가 CSV로 내는 것과 같은 함수·같은 좌표
    sf = pd.DataFrame(dict(t=t, lat=lat.where(map_ok), lon=lon.where(map_ok), cell=cell,
                           arfcn=pd.to_numeric(col(df, "serving_freq_arfcn"), errors="coerce"),
                           rsrp=rsrp, sinr=pd.to_numeric(col(df, "sinr_snr_db"), errors="coerce"),
                           speed=speed, rat=rat, pp=pp.to_numpy()))
    segs = {lv: cell_stats.segments(sf, lv) for lv in ("cell", "enb")}

    ncols = len(pd.read_csv(s["path"], nrows=0).columns)
    ver = VERSIONS.get(ncols, VERSIONS[max(VERSIONS)])

    return dict(
        id=idx, label=s["label"], note=s["note"], file=os.path.basename(s["path"]), t0=t0,
        activity=s["activity"], activity_ko=ACTIVITY_KO[s["activity"]],
        dur_min=round(dur_min, 1), raw_min=round(raw_min, 1), km=round(km, 1), rows=len(df),
        cut_head=round(cut_head, 1), cut_tail=round(cut_tail, 1), trim_reason=s["trim_reason"],
        version=ver["name"], ncols=ncols, coord_src=coord_src,
        frozen_share=round(100.0 * frozen.mean(), 1),
        has_sinr=bool(col(df, "sinr_snr_db").notna().mean() > 0.5),
        bins=make_bins(t, ho, pp, rsrp, rx, cell),
        pts=pts, events=events, segs=segs, speedtests=speedtests, rat_s=cell_stats.rat_seconds(sf),
    )


def build(scope, sessions, html):
    out, eyebrow, title = OUTS[scope]
    picked = [s for s in sessions if scope == "all" or s["activity"] == scope]
    # 리포트마다 id를 0부터 다시 매긴다 (템플릿이 SESS[id]로 참조한다)
    picked = [dict(s, id=i, segs={lv: [[g[c] for c in SEG_COLS] for g in s["segs"][lv]] for lv in s["segs"]})
              for i, s in enumerate(picked)]
    used = sorted({s["ncols"] for s in picked})
    data = dict(
        cols=["t", "lat", "lon", "cell", "rsrp", "sinr", "rsrq", "nbr", "nbrx", "speed", "rx", "tx",
              "pci", "arfcn", "ho", "pp", "rat", "rtt", "nbr_cell", "nbr_conf"],
        seg_cols=SEG_COLS,
        st_cols=["t", "mbps", "rsrp", "sinr", "speed", "cell_changed", "rat", "after_ho"],
        ev_cols=["t", "from", "to", "same_enb", "rsrp_prev", "rsrp_new", "speed", "pp",
                 "rank", "nsame", "loss"],
        meta=dict(scope=scope, eyebrow="NetworkTrackerApp · " + eyebrow, title=title,
                  kind={"all": "차량·지하철·도보로 이동하거나 머물며", "car": "차량으로 이동하며",
                        "subway": "지하철로 이동하며", "walking": "걸어서 이동하며"}[scope]),
        versions=[dict(VERSIONS[c], cols=c,
                       added=ADDED.get(c, []),
                       files=[s["label"] for s in picked if s["ncols"] == c]) for c in used],
        sessions=picked,
        # 섹터 판별 기준 (전 세션 기준, analysis/sector_rules.py가 만든다) — 리포트 그림 17
        sector=json.load(open(SECTOR_JSON, encoding="utf-8")) if os.path.exists(SECTOR_JSON) else None,
        # 이웃 셀 ID 추정 검증·범위와 OpenCellID 조사 (neighbor_eci.py) — 그림 17
        nbr_eci=json.load(open(NBR_JSON, encoding="utf-8")) if os.path.exists(NBR_JSON) else None,
    )
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(html.replace("/*__DATA__*/null", payload))
    print(f"  → {out} ({os.path.getsize(out) / 1024:.0f} KB, 세션 {len(picked)}개)")


def build_index(sessions):
    """viz_build/index.html — 리포트·결과 CSV·세션 목록을 한 화면에. 빌드할 때마다 세션 목록이 갱신된다."""
    import datetime, html as H
    with open(INDEX_TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    rows = []
    for s in sessions:
        rows.append(
            "      <tr><td>" + H.escape(s["label"]) + "</td><td>" + s["activity_ko"] + "</td><td>" + s["version"] + "</td>"
            f"<td class=\"num\">{s['dur_min']:.1f}</td><td class=\"num\">{s['km']:.1f}</td><td class=\"num\">{len(s['events'])}</td>"
            f"<td class=\"num\">{len(s['speedtests']) or '–'}</td>"
            f"<td><a href=\"../trackingcsv/new/{H.escape(s['file'])}\">CSV</a></td></tr>")
    n_act = lambda a: sum(1 for s in sessions if s["activity"] == a)
    apps = [f"      <li><b>{v['name']}</b> ({c}개 항목) — {H.escape(v['summary'])}</li>" for c, v in sorted(VERSIONS.items())]
    fill = {
        "UPDATED": datetime.date.today().isoformat(),
        "N_SESS": str(len(sessions)), "N_ALL": str(len(sessions)),
        "HOURS": f"{sum(s['dur_min'] for s in sessions) / 60:.1f}",
        "ROWS": f"{sum(s['rows'] for s in sessions):,}",
        "HO": f"{sum(len(s['events']) for s in sessions):,}",
        "N_CAR": str(n_act("car")), "N_SUBWAY": str(n_act("subway")), "N_WALKING": str(n_act("walking")),
        "SESSION_ROWS": "\n".join(rows), "APP_ITEMS": "\n".join(apps),
    }
    for k, v in fill.items():
        page = page.replace("{{" + k + "}}", v)
    with open(INDEX_OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"  → {INDEX_OUT} (세션 {len(sessions)}개)")


def main():
    nbr_index = build_nbr_index()
    sessions = [load(s, i, nbr_index) for i, s in enumerate(SESSIONS)]
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
    build_index(sessions)


if __name__ == "__main__":
    main()
