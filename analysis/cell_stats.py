"""
셀 유지 통계 — 모델 연구용 기초 데이터셋 (2026-10-02 교수님 피드백 B·C)

한 셀(또는 한 기지국)을 잡은 순간부터 다른 셀로 넘어갈 때까지를 '유지 구간' 한 행으로 만든다.
리포트(build_viz.py)와 이 스크립트가 같은 함수(segments)를 써서, 화면에 보이는 숫자와
CSV 숫자가 항상 같다.

정의
  셀        = serving_cell_id (ECI). 셀 ID가 비어 있는 행은 건너뛰고 앞 셀이 이어진 것으로 본다.
  기지국    = ECI // 256 (eNB). 기존 리포트·intra_enb.py와 같은 기준.
  유지 시간 = 이 셀의 첫 측정 ~ 다음 셀의 첫 측정(= 핸드오버 시각). 5초 수집이라 ±5초 오차.
  잘림      = 세션의 첫 구간과 마지막 구간은 측정 시작·종료에 잘려 실제 길이를 모른다.
              censored=1로 표시하고 분포·평균에서는 뺀다.
  직선 거리 = 구간 안 첫 좌표 ~ 마지막 좌표(다음 셀 첫 좌표 포함, 즉 핸드오버 지점까지).
  경로 거리 = 같은 범위의 좌표를 차례로 이은 누적 거리.
              좌표는 리포트 지도와 같다 — 차량·도보는 정확도 50 m 이하·동결 아닌 GPS, 지하철은 역 보정.
              좌표가 2개 미만이면 거리는 비운다.
  핸드오버 종류 (구간이 끝날 때 어디로 갔는가)
    inter   = 다른 기지국으로
    band    = 같은 기지국, 다른 주파수(ARFCN) — 밴드만 바꿈
    sector  = 같은 기지국, 같은 주파수 — 섹터(방향)만 바꿈
  망(rat)   = override_network_type: NR_NSA → 5G(NSA), LTE_CA → LTE-A, 그 밖 → LTE.
              앱이 5G 셀 자체(is_5g_actual, nr_cell_seen)는 한 번도 잡지 못해(전 세션 0%)
              '화면 표시 기준'만 낼 수 있다.

사용법:
    python analysis/cell_stats.py
출력: analysis_output/stats/
    cell_segments.csv   셀 유지 구간 (행 = 구간, 모델 입력용)
    enb_segments.csv    기지국 유지 구간
    summary.csv         이동수단별 요약 (셀/기지국 × 유지 시간·거리)
    handover_types.csv  이동수단별 핸드오버 종류
    rat_share.csv       세션·이동수단별 5G/LTE-A/LTE 시간 비율
"""
import os
import numpy as np
import pandas as pd

OUTDIR = "analysis_output/stats"
MAX_DT_S = 10          # 표본 하나가 대표하는 시간 상한 (리포트 p.dt와 같다)
SHORT_S = 30           # 이보다 짧게 유지하면 '짧은 유지' (핑퐁 판정 창과 같은 30초)
LONG_S = 300           # 이보다 길게 유지하면 '긴 유지'
RAT_KO = {2: "5G(NSA)", 1: "LTE-A", 0: "LTE"}


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * np.arcsin(np.sqrt(a))


def rat_code(override):
    s = override.astype(str).str.upper()
    return np.where(s.eq("NR_NSA"), 2, np.where(s.eq("LTE_CA"), 1, 0))


def _dist(lat, lon):
    """(직선, 경로) m. 좌표가 2개 미만이면 (nan, nan)."""
    ok = ~(np.isnan(lat) | np.isnan(lon))
    la, lo = lat[ok], lon[ok]
    if len(la) < 2:
        return np.nan, np.nan
    straight = float(haversine_m(la[0], lo[0], la[-1], lo[-1]))
    path = float(np.sum(haversine_m(la[:-1], lo[:-1], la[1:], lo[1:])))
    return straight, path


def _runs(key):
    """같은 key가 이어지는 구간 [(시작 idx, 끝 idx)] — key가 NaN인 행은 앞 값에 붙인다."""
    k = pd.Series(key).ffill().bfill().to_numpy()
    out, s = [], 0
    for i in range(1, len(k) + 1):
        if i == len(k) or k[i] != k[s]:
            out.append((s, i - 1))
            s = i
    return out, k


def segments(f, level="cell"):
    """세션 한 개의 유지 구간 목록.

    f: 열 t(초), lat, lon, cell, arfcn, rsrp, sinr, speed, rat, pp 를 가진 DataFrame
       (lat/lon은 지도에 쓰는 좌표 — 못 쓰는 행은 NaN)
    level: "cell" 또는 "enb"
    """
    cell = f["cell"].to_numpy(dtype=float)
    if np.all(np.isnan(cell)):
        return []
    key = cell if level == "cell" else np.floor(cell / 256)
    runs, kf = _runs(key)
    cellf = pd.Series(cell).ffill().bfill().to_numpy()
    arf = pd.Series(f["arfcn"].to_numpy(dtype=float)).ffill().bfill().to_numpy()
    t = f["t"].to_numpy(dtype=float)
    lat, lon = f["lat"].to_numpy(dtype=float), f["lon"].to_numpy(dtype=float)
    dt = np.clip(np.diff(t, append=t[-1] + 5), 0, MAX_DT_S)
    out = []
    for j, (a, b) in enumerate(runs):
        nxt = runs[j + 1][0] if j + 1 < len(runs) else None
        t_end = t[nxt] if nxt is not None else t[b]
        hi = nxt if nxt is not None else b          # 거리는 핸드오버 지점(다음 셀 첫 좌표)까지
        straight, path = _dist(lat[a:hi + 1], lon[a:hi + 1])
        sl = slice(a, b + 1)
        sp = f["speed"].to_numpy(dtype=float)[sl]
        rs = f["rsrp"].to_numpy(dtype=float)[sl]
        rat = f["rat"].to_numpy()[sl]
        w = dt[sl]
        end_type = ""
        if nxt is not None:
            c0, c1 = cellf[b], cellf[nxt]
            if c0 // 256 != c1 // 256:
                end_type = "inter"
            elif arf[b] != arf[nxt]:
                end_type = "band"
            else:
                end_type = "sector"
        out.append(dict(
            level=level,
            key=int(kf[a]),
            enb=int(cellf[a] // 256),
            t_start=round(float(t[a]), 1),
            t_end=round(float(t_end), 1),
            dwell_s=round(float(t_end - t[a]), 1),
            censored=int(j == 0 or nxt is None),
            n_rows=int(b - a + 1),
            n_cells=int(len(set(cellf[sl]))),
            n_intra_ho=int(np.sum(cellf[a + 1:b + 1] != cellf[a:b])) if level == "enb" else 0,
            straight_m=None if np.isnan(straight) else round(straight, 1),
            path_m=None if np.isnan(path) else round(path, 1),
            coord_share=round(float(np.mean(~np.isnan(lat[sl]))), 2),
            speed_mean=None if np.all(np.isnan(sp)) else round(float(np.nanmean(sp)), 2),
            rsrp_mean=None if np.all(np.isnan(rs)) else round(float(np.nanmean(rs)), 1),
            rsrp_min=None if np.all(np.isnan(rs)) else float(np.nanmin(rs)),
            rsrp_first=None if np.isnan(rs[0]) else float(rs[0]),
            rsrp_last=None if np.isnan(rs[-1]) else float(rs[-1]),
            nr_share=round(float(np.sum(w * (rat == 2)) / max(np.sum(w), 1e-9)), 2),
            arfcn=None if np.isnan(arf[a]) else int(arf[a]),
            end_type=end_type,
            end_pp=int(bool(f["pp"].to_numpy()[nxt])) if nxt is not None else 0,
        ))
    return out


def rat_seconds(f):
    """망 종류별 머문 시간(초) — {2: s, 1: s, 0: s}"""
    t = f["t"].to_numpy(dtype=float)
    dt = np.clip(np.diff(t, append=t[-1] + 5), 0, MAX_DT_S)
    rat = f["rat"].to_numpy()
    return {k: float(np.sum(dt[rat == k])) for k in (2, 1, 0)}


def _summ(df, by):
    rows = []
    for k, g in df.groupby(by):
        u = g[g.censored == 0]
        d = u.dwell_s
        rows.append(dict(
            group=k, segments=len(g), used=len(u),
            dwell_mean=round(d.mean(), 1), dwell_median=round(d.median(), 1),
            dwell_p10=round(d.quantile(.1), 1), dwell_p90=round(d.quantile(.9), 1),
            dwell_min=d.min(), dwell_max=d.max(),
            short_share=round((d < SHORT_S).mean(), 3), long_share=round((d >= LONG_S).mean(), 3),
            straight_median=round(u.straight_m.median(), 1), path_median=round(u.path_m.median(), 1),
            path_mean=round(u.path_m.mean(), 1), dist_n=int(u.path_m.notna().sum()),
        ))
    return pd.DataFrame(rows)


def main():
    import build_viz as bv
    os.makedirs(OUTDIR, exist_ok=True)
    cs, es, rs = [], [], []
    for i, s in enumerate(bv.SESSIONS):
        sess = bv.load(s, i)
        meta = dict(session=os.path.splitext(sess["file"])[0], label=sess["label"], activity=s["activity"])
        for seg in sess["segs"]["cell"]:
            cs.append({**meta, **seg})
        for seg in sess["segs"]["enb"]:
            es.append({**meta, **seg})
        tot = sum(sess["rat_s"].values()) or 1
        rs.append({**meta, **{RAT_KO[k] + "_s": round(v, 0) for k, v in sess["rat_s"].items()},
                   **{RAT_KO[k] + "_share": round(v / tot, 3) for k, v in sess["rat_s"].items()}})
    cdf, edf, rdf = pd.DataFrame(cs), pd.DataFrame(es), pd.DataFrame(rs)
    cdf.to_csv(os.path.join(OUTDIR, "cell_segments.csv"), index=False, encoding="utf-8-sig")
    edf.to_csv(os.path.join(OUTDIR, "enb_segments.csv"), index=False, encoding="utf-8-sig")

    summ = pd.concat([_summ(cdf, "activity").assign(level="cell"),
                      _summ(edf, "activity").assign(level="enb")])
    summ.to_csv(os.path.join(OUTDIR, "summary.csv"), index=False, encoding="utf-8-sig")

    ho = cdf[cdf.end_type != ""].groupby(["activity", "end_type"]).size().unstack(fill_value=0)
    ho["total"] = ho.sum(axis=1)
    ho.to_csv(os.path.join(OUTDIR, "handover_types.csv"), encoding="utf-8-sig")

    act = rdf.groupby("activity")[[RAT_KO[k] + "_s" for k in (2, 1, 0)]].sum()
    act = act.div(act.sum(axis=1), axis=0).round(3)
    pd.concat([rdf, act.reset_index().assign(label="(이동수단 합계)")]).to_csv(
        os.path.join(OUTDIR, "rat_share.csv"), index=False, encoding="utf-8-sig")

    print(f"셀 구간 {len(cdf)}개, 기지국 구간 {len(edf)}개 → {OUTDIR}/")
    print(summ[["level", "group", "used", "dwell_mean", "dwell_median", "short_share",
                "long_share", "straight_median", "path_median"]].to_string(index=False))
    print(ho.to_string())
    print(act.to_string())


if __name__ == "__main__":
    main()
