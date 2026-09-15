"""
지하철 구간 역 단위 위치 보간 (사후 맵매칭, CHANGES_GPS_IMPROVEMENT.md §6)

원리:
  1. anchor 행(collect_trigger="anchor")의 역명을 공공데이터 역 좌표로 치환
     - 카카오 조회 좌표는 환승역에서 다른 호선이 잡히므로 쓰지 않음
     - 호선은 앵커마다 정함: 환승 횟수가 가장 적은 호선 조합을 고름 (환승 대응)
     - 역 데이터에 없는 역은 카카오 좌표로 대체
  2. 정차 감지: 기압 20초 창 표준편차 < 10 Pa 가 10초 이상 이어지면 정차
     - 터널 주행 중엔 피스톤 효과로 기압이 수십 Pa씩 출렁이고, 역에 서면 잠잠해짐
     - 지상 구간은 달려도 기압이 잠잠할 수 있으므로, 좋은 GPS가 3 m/s 넘게
       달리고 있다고 하는 구간은 정차에서 뺌
  3. 정차 구간 ↔ 역 연결
     - 앵커 시각이 정차 구간 안이거나 45초 이내면 그 역의 정차로 봄
     - 태그 없는 정차는 두 역 사이 역 개수와 정차 개수가 같을 때만 순서대로 배정
     - 두 역 사이 역 = 같은 호선에서 두 역을 잇는 직선 가까이 있는 역 (지선·순환선 무관)
  4. 위치 할당
     - 좋은 GPS(실측 속도 있음, 정확도 25 m 이하, 5초 이내, 앞뒤 역과 모순 없음) → GPS 그대로
     - 정차 중(도착~출발)       → 역 좌표 고정
     - 출발 ~ 다음 역 도착 사이 → 역 경로(직선 연결) 위 시간 비례 (환승 구간은 두 역 직선)
     - 그 밖 → 공백
  5. 검증(leave-one-out): 앵커 하나를 빼고 나머지로 그 역에 서 있던 시점 위치를 추정,
     역 좌표와의 오차를 '앵커만' 방식과 '앵커+정차' 방식으로 비교

역 좌표: reference/seoul_station_master_tdata.csv
  (서울교통빅데이터플랫폼 T-Data "지하철역_GEOM (역사마스터)", CC BY — 출처 표시 필요)

사용법:
    python analysis/station_mapmatch.py trackingcsv/new/network_log_20260909_165646_subway.csv
    python analysis/station_mapmatch.py            # 인자 없으면 trackingcsv/new/*subway*.csv
출력: analysis_output/<파일명>_mapmatched.csv, <파일명>_mapmatched_map.html
"""
import sys, os, re, glob
from collections import Counter
import numpy as np
import pandas as pd
import folium

STATION_CSV = "reference/seoul_station_master_tdata.csv"
OUTDIR = "analysis_output"

STOP_WINDOW = "20s"      # 기압 표준편차 계산 창
STOP_STD_PA = 10.0       # 이보다 잠잠하면 정차
MIN_STOP_S = 10          # 최소 정차 길이
ANCHOR_STOP_GAP_S = 45   # 앵커 태그와 정차 구간이 이만큼 떨어져 있어도 같은 역으로 봄
GOOD_GPS_ACC_M = 25      # 좋은 GPS 정확도 상한
GOOD_GPS_MAX_AGE_S = 5   # 좋은 GPS 나이 상한
MOVING_SPEED_MS = 3.0    # 이보다 빠르면 정차 아님
MAX_TRAIN_SPEED_MS = 30  # GPS와 역 위치 모순 판정용 (약 108 km/h)
BETWEEN_MAX_OFF_M = 400  # 두 역을 잇는 직선에서 이만큼(또는 구간 길이의 25%) 안이면 사이 역
BETWEEN_MAX_SPAN_M = 4000  # 두 역이 이보다 멀면 사이 역 추정 안 함 (태그는 3~4역마다 한 번 권장)

# 운영 구간명 → 승객이 부르는 노선명 (같은 열차가 이어 달리는 구간끼리 묶음)
LINE_GROUPS = {
    "경부선": "1호선", "경원선": "1호선", "경인선": "1호선", "장항선": "1호선",
    "일산선": "3호선",
    "과천선": "4호선", "안산선": "4호선", "진접선": "4호선",
    "별내선": "8호선",
    "중앙선": "경의중앙선",
    "분당선": "수인분당선", "수인선": "수인분당선",
}

# 본선과 나란히 달려서 좌표만으로는 구분이 안 되는 지선: [분기역, 지선역, ...] 진행 순서
BRANCH_ROUTES = {
    "2호선": [
        ["성수", "용답", "신답", "용두", "신설동"],                 # 성수지선
        ["신도림", "도림천", "양천구청", "신정네거리", "까치산"],   # 신정지선
    ],
}


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000.0 * np.arcsin(np.sqrt(a))


def norm_name(s):
    """'을지로3가역 2호선' → '을지로3가', '새절(신사)' → '새절'."""
    s = re.sub(r"\s*\d+호선\s*$", "", str(s).strip())
    s = re.sub(r"\(.*?\)", "", s).strip()
    return s[:-1] if s.endswith("역") and len(s) > 1 else s


def norm_line(s):
    """'9호선(연장)' → '9호선', '경부선' → '1호선'."""
    base = re.sub(r"\(.*?\)", "", str(s)).strip()
    return LINE_GROUPS.get(base, base)


def load_stations():
    st = pd.read_csv(STATION_CSV, encoding="utf-8-sig")
    st = st.rename(columns={"외구간_역_수": "code", "역한글명칭": "name", "호선명칭": "line_raw",
                            "환승역X좌표": "lon", "환승역Y좌표": "lat"})
    st["key"] = st["name"].map(norm_name)
    st["line"] = st["line_raw"].map(norm_line)
    # 지선 역이 해당 호선 이름으로 안 올라와 있으면(예: 까치산은 5호선으로만 등록) 좌표를 빌려 추가
    extra = []
    for line, routes in BRANCH_ROUTES.items():
        for k in {k for r in routes for k in r}:
            if not ((st["line"] == line) & (st["key"] == k)).any():
                src = st[st["key"] == k]
                if len(src):
                    extra.append({**src.iloc[0].to_dict(), "line": line, "line_raw": line})
    if extra:
        st = pd.concat([st, pd.DataFrame(extra)], ignore_index=True)
    return st.drop_duplicates(["line", "key"])[["line", "line_raw", "code", "name", "key", "lat", "lon"]]


def assign_lines(keys, st):
    """앵커 역명 순서 → 앵커별 호선. 환승 횟수 최소, 동률이면 자주 나오는 호선."""
    cand = [sorted(st.loc[st["key"] == k, "line"].unique()) for k in keys]
    freq = Counter(l for c in cand for l in c)
    INF = (10 ** 9, 0)
    prev = {}
    back = []
    for i, c in enumerate(cand):
        cur, bp = {}, {}
        for line in c:
            if not prev:
                cur[line], bp[line] = (0, -freq[line]), None
                continue
            best = min(prev, key=lambda p: (prev[p][0] + (p != line), prev[p][1]))
            cur[line] = (prev[best][0] + (best != line), prev[best][1] - freq[line])
            bp[line] = best
        if not c:            # 역 데이터에 없는 역: 이전 상태를 그대로 넘김
            cur, bp = dict(prev), {p: p for p in prev}
        back.append(bp)
        prev = cur
    out = [None] * len(cand)
    if not prev:
        return out
    line = min(prev, key=lambda p: prev[p])
    for i in range(len(cand) - 1, -1, -1):
        out[i] = line if cand[i] else None
        line = back[i].get(line, line) if line is not None else None
    return out


def _branch_of(key, line):
    for route in BRANCH_ROUTES.get(line, []):
        if key in route[1:]:
            return route
    return None


def _station(key, line, st):
    r = st[(st["line"] == line) & (st["key"] == key)].iloc[0]
    return dict(key=key, lat=r["lat"], lon=r["lon"], line=line)


def between_stations(A, B, st):
    """같은 호선에서 A→B 사이 역들 (A, B 제외, 진행 순서)."""
    if A["line"] is None or A["line"] != B["line"]:
        return []
    line = A["line"]

    # 지선: 정해진 순서를 따르고, 본선으로 넘어갈 땐 분기역을 거침
    ra, rb = _branch_of(A["key"], line), _branch_of(B["key"], line)
    if ra is not None and ra is rb:
        i, j = ra.index(A["key"]), ra.index(B["key"])
        seq = ra[i + 1:j] if i < j else ra[j + 1:i][::-1]
        return [_station(k, line, st) for k in seq]
    if ra is not None:
        J = _station(ra[0], line, st)
        up = [_station(k, line, st) for k in ra[1:ra.index(A["key"])][::-1]]
        return up + ([] if B["key"] == J["key"] else [J] + between_stations(J, B, st))
    if rb is not None:
        J = _station(rb[0], line, st)
        down = [_station(k, line, st) for k in rb[1:rb.index(B["key"])]]
        return ([] if A["key"] == J["key"] else between_stations(A, J, st) + [J]) + down

    # 본선: 두 역을 잇는 직선 가까이 있는 역 (지선 전용 역은 제외)
    branch_only = {k for r in BRANCH_ROUTES.get(line, []) for k in r[1:]}
    ky = 110540.0
    kx = 111320.0 * np.cos(np.radians((A["lat"] + B["lat"]) / 2))
    ax, ay, bx, by = A["lon"] * kx, A["lat"] * ky, B["lon"] * kx, B["lat"] * ky
    L2 = (bx - ax) ** 2 + (by - ay) ** 2
    if L2 == 0:
        return []
    L = np.sqrt(L2)
    # 멀리 떨어진 두 역은 선로가 휘어서 직선 근처 판정이 틀림 → 사이 역을 추정하지 않음
    if L > BETWEEN_MAX_SPAN_M:
        return []
    g = st[(st["line"] == line) & ~st["key"].isin([A["key"], B["key"]] + sorted(branch_only))]
    sx, sy = g["lon"].to_numpy() * kx, g["lat"].to_numpy() * ky
    t = ((sx - ax) * (bx - ax) + (sy - ay) * (by - ay)) / L2
    off = np.abs((sx - ax) * (by - ay) - (sy - ay) * (bx - ax)) / L
    m = (t > 0.05) & (t < 0.95) & (off < max(BETWEEN_MAX_OFF_M, 0.25 * L))
    mids = g[m].assign(t=t[m]).sort_values("t")
    return [dict(key=r.key, lat=r.lat, lon=r.lon, line=A["line"]) for r in mids.itertuples()]


def interp_on_path(path, frac):
    """경로(좌표 리스트) 전체 길이의 frac 지점 좌표."""
    lat = np.array([p["lat"] for p in path]); lon = np.array([p["lon"] for p in path])
    d = haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
    cum = np.concatenate([[0], np.cumsum(d)])
    if cum[-1] == 0:
        return lat[0], lon[0]
    target = frac * cum[-1]
    i = min(np.searchsorted(cum, target, side="right") - 1, len(d) - 1)
    f = 0.0 if d[i] == 0 else (target - cum[i]) / d[i]
    return lat[i] + f * (lat[i + 1] - lat[i]), lon[i] + f * (lon[i + 1] - lon[i])


def build_anchors(df, st):
    a = df[df["collect_trigger"] == "anchor"].sort_values("timestamp").reset_index(drop=True)
    a["key"] = a["anchor_station"].map(norm_name)
    a["line"] = assign_lines(a["key"].tolist(), st)
    lat, lon, src = [], [], []
    for r in a.itertuples():
        hit = st[(st["line"] == r.line) & (st["key"] == r.key)] if r.line else st.iloc[0:0]
        if len(hit):
            lat.append(hit["lat"].iloc[0]); lon.append(hit["lon"].iloc[0]); src.append("public")
        elif pd.notna(r.anchor_lat):
            lat.append(r.anchor_lat); lon.append(r.anchor_lon); src.append("kakao")
            print(f"  ! 역 데이터에 없는 역, 카카오 좌표 사용: {r.anchor_station}")
        else:
            lat.append(np.nan); lon.append(np.nan); src.append("")
            print(f"  ! 좌표를 알 수 없는 앵커, 제외: {r.anchor_station}")
    a["st_lat"], a["st_lon"], a["coord_src"] = lat, lon, src
    return a.dropna(subset=["st_lat"]).reset_index(drop=True)


def good_gps_mask(df):
    m = df["gps_speed_ms"].notna() & (df["gps_accuracy_m"] <= GOOD_GPS_ACC_M)
    if "location_source" in df.columns:
        m &= ~df["location_source"].astype(str).str.startswith("stale")
    if "location_age_s" in df.columns:
        m &= df["location_age_s"].fillna(0) <= GOOD_GPS_MAX_AGE_S
    return m.to_numpy()


def detect_stops(df, good):
    """기압이 잠잠한 구간 = 정차. [[시작ms, 끝ms], ...]"""
    if "pressure_hpa" not in df.columns:
        return []
    s = (df[["timestamp", "pressure_hpa"]].dropna()
         .drop_duplicates("timestamp").sort_values("timestamp"))
    if len(s) < 5:
        return []
    p = pd.Series(s["pressure_hpa"].to_numpy(), index=pd.to_datetime(s["timestamp"], unit="ms"))
    still = ((p.rolling(STOP_WINDOW, center=True).std() * 100) < STOP_STD_PA).to_numpy()
    ts = s["timestamp"].to_numpy()
    stops, start = [], None
    for i, v in enumerate(still):
        if v and start is None:
            start = i
        if start is not None and (not v or i == len(still) - 1):
            end = i if v else i - 1
            if (ts[end] - ts[start]) / 1000 >= MIN_STOP_S:
                stops.append([int(ts[start]), int(ts[end])])
            start = None

    # 지상 구간: 좋은 GPS가 달리고 있다고 하면 정차가 아님
    rts, spd = df["timestamp"].to_numpy(), df["gps_speed_ms"].to_numpy()
    kept = []
    for s0, s1 in stops:
        m = (rts >= s0) & (rts <= s1) & good
        if m.sum() >= 2 and np.nanmedian(spd[m]) > MOVING_SPEED_MS:
            continue
        kept.append([s0, s1])
    return kept


def build_events(anchors, stops, st):
    """역 체류 이벤트 목록: key, line, lat, lon, t_arr, t_dep, src."""
    ev, used = [], set()
    for _, a in anchors.iterrows():
        t = a["timestamp"]
        best, best_gap = None, None
        for j, (s0, s1) in enumerate(stops):
            if j in used:
                continue
            gap = 0 if s0 <= t <= s1 else min(abs(t - s0), abs(t - s1))
            if gap <= ANCHOR_STOP_GAP_S * 1000 and (best_gap is None or gap < best_gap):
                best, best_gap = j, gap
        if best is not None:
            used.add(best)
            t0, t1 = stops[best]
            src = "anchor+stop"
        else:
            t0 = t1 = t
            src = "anchor"
        ev.append(dict(key=a["key"], line=a["line"], lat=a["st_lat"], lon=a["st_lon"],
                       t_arr=t0, t_dep=t1, src=src))
    ev.sort(key=lambda e: e["t_arr"])

    # 태그 없는 정차 → 사이 역에 순서대로 배정 (개수가 맞을 때만)
    extra = []
    for A, B in zip(ev[:-1], ev[1:]):
        mids = between_stations(A, B, st)
        free = [s for j, s in enumerate(stops)
                if j not in used and A["t_dep"] < s[0] and s[1] < B["t_arr"]]
        if mids and len(free) == len(mids):
            for m, (s0, s1) in zip(mids, free):
                extra.append(dict(m, t_arr=s0, t_dep=s1, src="stop_inferred"))
    return sorted(ev + extra, key=lambda e: e["t_arr"])


def positions(ts, ev, st):
    """timestamp 배열 → (lat, lon, method, prev_station, next_station)."""
    n = len(ts)
    lat = np.full(n, np.nan); lon = np.full(n, np.nan)
    method = np.array(["none"] * n, dtype=object)
    prev_s = np.array([""] * n, dtype=object); next_s = np.array([""] * n, dtype=object)
    for e in ev:
        m = (ts >= e["t_arr"]) & (ts <= e["t_dep"])
        lat[m], lon[m], method[m] = e["lat"], e["lon"], "station"
        prev_s[m], next_s[m] = e["key"], e["key"]
    for A, B in zip(ev[:-1], ev[1:]):
        path = [A] + between_stations(A, B, st) + [B]
        span = B["t_arr"] - A["t_dep"]
        for j in np.where((ts > A["t_dep"]) & (ts < B["t_arr"]))[0]:
            lat[j], lon[j] = interp_on_path(path, (ts[j] - A["t_dep"]) / span)
            method[j] = "between" if A["line"] == B["line"] else "transfer"
            prev_s[j], next_s[j] = A["key"], B["key"]
    return lat, lon, method, prev_s, next_s


def gps_consistent(df, good, ev):
    """좋은 GPS라도 가장 가까운 역 이벤트와 시간·거리가 모순이면 버림 (앱 시작 시 옛날 위치 등)."""
    ok = good.copy()
    if not ev:
        return ok
    t_mid = np.array([(e["t_arr"] + e["t_dep"]) / 2 for e in ev])
    for j in np.where(good)[0]:
        t = df["timestamp"].iat[j]
        e = ev[int(np.argmin(np.abs(t_mid - t)))]
        dt = 0 if e["t_arr"] <= t <= e["t_dep"] else min(abs(t - e["t_arr"]), abs(t - e["t_dep"])) / 1000
        d = haversine_m(df["latitude"].iat[j], df["longitude"].iat[j], e["lat"], e["lon"])
        if d > MAX_TRAIN_SPEED_MS * dt + 500:
            ok[j] = False
    return ok


def leave_one_out(anchors, stops, st):
    """앵커 k를 빼고, 그 역에 서 있던 시점(정차 중간, 없으면 태그 시각)의 추정 오차."""
    full = build_events(anchors, stops, st)
    rows = []
    for k in range(1, len(anchors) - 1):
        key = anchors.loc[k, "key"]
        e = next((x for x in full if x["key"] == key and x["src"] != "stop_inferred"), None)
        t_eval = (e["t_arr"] + e["t_dep"]) / 2 if e else anchors.loc[k, "timestamp"]
        rest = anchors.drop(index=k).reset_index(drop=True)
        errs = []
        for use_stops in ([], stops):
            la, lo, *_ = positions(np.array([t_eval]), build_events(rest, use_stops, st), st)
            errs.append(haversine_m(la[0], lo[0], anchors.loc[k, "st_lat"], anchors.loc[k, "st_lon"]))
        rows.append((key, *errs))
    return rows


def fmt_t(ms):
    return pd.to_datetime(ms, unit="ms").tz_localize("UTC").tz_convert("Asia/Seoul").strftime("%H:%M:%S")


def run(csv, st):
    name = os.path.splitext(os.path.basename(csv))[0]
    df = pd.read_csv(csv, low_memory=False)
    print(f"\n== {name}  ({len(df)} rows)")
    if "anchor_station" not in df.columns or (df["collect_trigger"] == "anchor").sum() < 2:
        print("  앵커 2개 미만 → 보간 불가, 건너뜀")
        return

    anchors = build_anchors(df, st)
    if len(anchors) < 2:
        print("  유효 앵커 2개 미만 → 건너뜀")
        return
    print("  앵커 호선: " + " → ".join(f"{r.key}({r.line or '?'})" for r in anchors.itertuples()))

    good = good_gps_mask(df)
    stops = detect_stops(df, good)
    events = build_events(anchors, stops, st)
    print(f"  기압 정차 감지: {len(stops)}구간" + ("" if "pressure_hpa" in df.columns else "  (pressure_hpa 없음 → 앵커만 사용)"))
    print("  역 체류 이벤트:")
    for e in events:
        tag = anchors.loc[anchors["key"] == e["key"], "datetime"]
        tag = tag.iloc[0][11:] if len(tag) else "-"
        print(f"    {e['key']:<8} {str(e['line']):<6} 도착 {fmt_t(e['t_arr'])} ~ 출발 {fmt_t(e['t_dep'])} "
              f"({(e['t_dep'] - e['t_arr']) / 1000:3.0f}s)  태그 {tag}  [{e['src']}]")

    ts = df["timestamp"].to_numpy()
    lat, lon, method, prev_s, next_s = positions(ts, events, st)
    use_gps = gps_consistent(df, good, events)
    lat[use_gps] = df["latitude"].to_numpy()[use_gps]
    lon[use_gps] = df["longitude"].to_numpy()[use_gps]
    method[use_gps] = "gps"
    if (good & ~use_gps).any():
        print(f"  ! 역 위치와 모순되는 GPS {int((good & ~use_gps).sum())}행 무시")

    df["lat_mm"], df["lon_mm"], df["mm_method"] = lat, lon, method
    df["mm_prev_station"], df["mm_next_station"] = prev_s, next_s
    df["mm_vs_gps_m"] = haversine_m(df["latitude"], df["longitude"], df["lat_mm"], df["lon_mm"])

    covered = df["lat_mm"].notna()
    cnt = Counter(method)
    print("  위치 할당: " + ", ".join(f"{k} {cnt[k]}행" for k in ["gps", "station", "between", "transfer", "none"] if cnt[k])
          + f" / {len(df)}")
    print(f"  할당 위치 ↔ 원래 GPS 거리: 중앙값 {df.loc[covered, 'mm_vs_gps_m'].median():.0f} m, "
          f"90% {df.loc[covered, 'mm_vs_gps_m'].quantile(0.9):.0f} m, 최대 {df.loc[covered, 'mm_vs_gps_m'].max():.0f} m")

    loo = leave_one_out(anchors, stops, st)
    if loo:
        print("  검증(leave-one-out, 앵커 하나 빼고 그 역에 서 있던 시점 추정 오차):")
        print(f"    {'역':<8} {'앵커만':>8} {'앵커+정차':>10}")
        for k, e0, e1 in loo:
            print(f"    {k:<8} {e0:6.0f} m {e1:8.0f} m")
        print(f"    {'중앙값':<8} {np.median([r[1] for r in loo]):6.0f} m {np.median([r[2] for r in loo]):8.0f} m")

    os.makedirs(OUTDIR, exist_ok=True)
    out_csv = os.path.join(OUTDIR, f"{name}_mapmatched.csv")
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    m = folium.Map(location=[anchors["st_lat"].mean(), anchors["st_lon"].mean()], zoom_start=14)
    folium.PolyLine(df[["latitude", "longitude"]].dropna().values.tolist(),
                    color="#d62728", weight=2, opacity=0.6, tooltip="원래 GPS").add_to(m)
    folium.PolyLine(df.loc[covered, ["lat_mm", "lon_mm"]].values.tolist(),
                    color="#1f77b4", weight=4, tooltip="보정 위치").add_to(m)
    for line in {e["line"] for e in events if e["line"]}:
        for s in st[st["line"] == line].itertuples():
            folium.CircleMarker([s.lat, s.lon], radius=3, color="#555", fill=True,
                                tooltip=f"{s.name} {s.line_raw}").add_to(m)
    for e in events:
        folium.Marker([e["lat"], e["lon"]],
                      tooltip=f"{e['key']} {fmt_t(e['t_arr'])}~{fmt_t(e['t_dep'])} [{e['src']}]").add_to(m)
    out_map = os.path.join(OUTDIR, f"{name}_mapmatched_map.html")
    m.save(out_map)
    print(f"  → {out_csv}\n  → {out_map}")


if __name__ == "__main__":
    st = load_stations()
    files = sys.argv[1:] or sorted(glob.glob("trackingcsv/new/*subway*.csv"))
    for f in files:
        run(f, st)
