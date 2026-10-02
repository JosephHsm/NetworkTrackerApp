"""
핸드오버 기준 대조 — 우리 측정이 표준(3GPP A3/A2·A4·A5)과 화웨이 기본값대로 움직이는가

부록 페이지(viz_build/handover_basics.html) 말단의 '우리 측정과 맞춰 보기'를 만드는 계산이다.
5초 간격 수집이라 A3의 320 ms 순간은 직접 못 본다 — 그래서 "직전 측정(≤5초 전)에 이미
조건이 성립해 있었는가", "조건이 성립한 뒤 얼마 안 가 실제로 옮겼는가"처럼 간접적으로 본다.

계산
  1. 같은 주파수 핸드오버(A3 대상): 직전 측정의 이웃 목록에서 옮겨간 셀 RSRP − 당시 접속 셀 RSRP = 여유(margin)
  2. A3 조건이 성립한 측정(같은 주파수 이웃이 접속 셀보다 +1/+3 dB 이상 셈) 뒤 10초 안에 실제 핸드오버가 났는가
  3. 다른 주파수로 옮긴 핸드오버(A2→A4/A5 대상): 옮기기 직전 접속 셀 RSRP·RSRQ가 같은 주파수 핸드오버 때보다 나빴는가
  4. 정지 세션에서도 위 규칙대로인가

사용법:
    python analysis/handover_rules.py
출력: analysis_output/stats/handover_rules.json (+ 콘솔 요약)
"""
import glob, json, os
import numpy as np
import pandas as pd

OUT = "analysis_output/stats/handover_rules.json"
BAND = {100: "B1 2.1GHz", 2600: "B5 850MHz", 3050: "B7 2.6GHz"}   # EARFCN → 대역 (36.101 표)
FOLLOW_S = 10        # A3 조건 성립 뒤 이 안에 옮기면 '따랐다'


def nbrs(js):
    try:
        a = json.loads(js)
        return [x for x in a if isinstance(x, dict) and x.get("rsrp") is not None]
    except Exception:
        return []


def activity(path):
    for a in ("subway", "car", "walking", "home"):
        if path.endswith(f"_{a}.csv"):
            return a
    return "car"      # 8월 _unknown 두 건은 차량 (build_viz.py 근거)


def q(a, p):
    a = [v for v in a if v is not None and not np.isnan(v)]
    return None if not a else round(float(np.percentile(a, p)), 1)


def main():
    ho_rows, cond_rows = [], []
    for f in sorted(glob.glob("trackingcsv/new/*.csv")):
        d = pd.read_csv(f, low_memory=False).sort_values("timestamp").reset_index(drop=True)
        if "prev_neighbors_json" not in d.columns:
            continue                        # v1.0 — 직전 이웃 목록 없음
        act = activity(f)
        t = d["timestamp"].to_numpy() / 1000.0
        ho = d["handover_detected"].astype(str).str.lower().eq("true").to_numpy()
        cell = pd.to_numeric(d["serving_cell_id"], errors="coerce").to_numpy()
        pci = pd.to_numeric(d["serving_pci"], errors="coerce").to_numpy()
        arf = pd.to_numeric(d["serving_freq_arfcn"], errors="coerce").to_numpy()
        rsrp = pd.to_numeric(d["rsrp_dbm"], errors="coerce").to_numpy()
        rsrq = pd.to_numeric(d["rsrq_db"], errors="coerce").to_numpy()
        ho_t = t[ho]

        # 2. A3 조건이 성립한 측정 → 10초 안에 핸드오버?
        for i in range(len(d)):
            if np.isnan(rsrp[i]) or np.isnan(arf[i]):
                continue
            same = [x["rsrp"] for x in nbrs(d.at[i, "neighbors_json"])
                    if x.get("earfcn") == arf[i] and x.get("pci") != pci[i]]
            if not same:
                continue
            gap = max(same) - rsrp[i]
            nxt = ho_t[ho_t > t[i]]
            cond_rows.append(dict(act=act, gap=gap, follow=bool(len(nxt) and nxt[0] - t[i] <= FOLLOW_S)))

        # 1·3. 핸드오버마다 직전 상태
        for i in np.where(ho)[0]:
            if i == 0 or np.isnan(cell[i]) or np.isnan(cell[i - 1]) or cell[i] == cell[i - 1]:
                continue
            j = i - 1
            pj = d.at[i, "prev_neighbors_json"]
            lst = nbrs(pj) if isinstance(pj, str) and pj.strip() not in ("", "[]") else nbrs(d.at[j, "neighbors_json"])
            tgt = [x["rsrp"] for x in lst if x.get("pci") == pci[i] and x.get("earfcn") == arf[i]]
            intra_f = arf[i] == arf[j]
            ho_rows.append(dict(
                act=act, intra_freq=bool(intra_f), same_enb=bool(cell[i] // 256 == cell[j] // 256),
                old_rsrp=rsrp[j], old_rsrq=rsrq[j], new_rsrp=rsrp[i], lag=t[i] - t[j],
                margin=(tgt[0] - rsrp[j]) if tgt else np.nan,
                pair=f"{BAND.get(int(arf[j]), int(arf[j]))} → {BAND.get(int(arf[i]), int(arf[i]))}" if not intra_f else "",
            ))

    H, Cd = pd.DataFrame(ho_rows), pd.DataFrame(cond_rows)
    A3 = H[H.intra_freq]
    m = A3.margin.dropna()
    res = {"n_ho": len(H), "n_intra_freq": len(A3), "n_inter_freq": int((~H.intra_freq).sum())}
    res["a3"] = dict(
        seen=int(len(m)), seen_share=round(len(m) / max(len(A3), 1), 3),
        margin_median=q(m, 50), margin_p25=q(m, 25), margin_p75=q(m, 75),
        ge0=round(float((m >= 0).mean()), 3), ge1=round(float((m >= 1).mean()), 3), ge3=round(float((m >= 3).mean()), 3),
        lt_minus3=round(float((m < -3).mean()), 3),
        lag_median=q(A3.lag, 50),
        by_act={a: dict(n=int(len(g.margin.dropna())), median=q(g.margin.dropna(), 50),
                        ge1=round(float((g.margin.dropna() >= 1).mean()), 3) if len(g.margin.dropna()) else None)
                for a, g in A3.groupby("act")},
        gain_median=q((A3.new_rsrp - A3.old_rsrp), 50),
    )
    cond = {}
    for thr in (1, 3, 6):
        c = Cd[Cd.gap >= thr]
        cond[f"ge{thr}"] = dict(rows=int(len(c)), share_of_rows=round(len(c) / max(len(Cd), 1), 3),
                                follow=round(float(c.follow.mean()), 3) if len(c) else None)
    c0 = Cd[Cd.gap < 0]
    cond["lt0"] = dict(rows=int(len(c0)), follow=round(float(c0.follow.mean()), 3) if len(c0) else None)
    cond["by_act_ge3"] = {a: dict(rows=int(len(g[g.gap >= 3])), follow=round(float(g[g.gap >= 3].follow.mean()), 3) if len(g[g.gap >= 3]) else None)
                          for a, g in Cd.groupby("act")}
    res["a3_condition"] = cond
    IF = H[~H.intra_freq]
    res["inter_freq"] = dict(
        n=int(len(IF)),
        old_rsrp_median=q(IF.old_rsrp, 50), old_rsrp_p25=q(IF.old_rsrp, 25), old_rsrp_p75=q(IF.old_rsrp, 75),
        old_rsrq_median=q(IF.old_rsrq, 50),
        intra_old_rsrp_median=q(A3.old_rsrp, 50), intra_old_rsrq_median=q(A3.old_rsrq, 50),
        pairs=IF.pair.value_counts().to_dict(),
        pair_rsrp={k: q(g.old_rsrp, 50) for k, g in IF.groupby("pair")},
        same_enb_share=round(float(IF.same_enb.mean()), 3) if len(IF) else None,
    )
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
