"""
핸드오버 데이터 점검 + 핸드오버/핑퐁 줄이는 규칙 재생 실험

1. 데이터 정합성 — 앱이 쓴 핸드오버·핑퐁 플래그가 원본 셀 변화와 맞는지, 음영(서비스 끊김) 흔적이 있는지
2. 핸드오버 개수 — 파일별 플래그 수 = 실제 셀 변경 수인지, 리포트(handover_types.csv)와 차이가 왜 나는지
3. 핸드오버 전후 RSRP — 핸드오버가 없는 5초 쌍(대조군)과 비교해서 핸드오버가 신호를 실제로 바꿨는지
4. 규칙 재생 — 측정된 서빙+이웃 RSRP를 시간순으로 다시 돌리며 "이 규칙이었으면 몇 번 옮겼고
   신호는 얼마였나"를 계산. 셀은 (EARFCN, PCI)로 식별 (이웃 cell_id는 전부 같은 값 — 이슈 7)

한계
  - 5초 간격이라 TTT(실제 40~5120 ms)는 '몇 행 연속'으로만 흉내 낸다.
  - 가상 셀이 이웃 목록에서 사라지면 RSRP를 알 수 없다 → 그 행에서 실제 망이 고른 셀로 강제 이동(forced)으로 센다.
  - cell_info_age_ms > 8000 인 행은 직전 값 재사용(동결)이라 판단에 쓰지 않는다.

사용법:
    python analysis/ho_review.py
출력: analysis_output/stats/ho_review.json (+ 콘솔 요약)
"""
import glob, json, os
import numpy as np
import pandas as pd

OUT = "analysis_output/stats/ho_review.json"
STALE_MS = 8000      # 이보다 오래된 allCellInfo는 동결 값
PP_S = 30            # 핑퐁 판정 창 (앱과 같다)
WEAK = (-105, -110)  # 약전계 기준 (dBm)


def activity(path):
    for a in ("subway", "car", "walking", "home"):
        if path.endswith(f"_{a}.csv"):
            return a
    return "car"      # 8월 _unknown 두 건은 차량 (build_viz.py 근거)


def as_bool(s):
    return s.astype(str).str.lower().eq("true").to_numpy()


def nbr_map(js):
    """이웃 JSON → {(earfcn, pci): rsrp} (LTE, RSRP 있는 것만)"""
    try:
        a = json.loads(js)
    except Exception:
        return {}
    return {(int(x["earfcn"]), int(x["pci"])): int(x["rsrp"]) for x in a
            if isinstance(x, dict) and x.get("type") == "LTE"
            and x.get("rsrp") is not None and x.get("pci") is not None and x.get("earfcn") is not None}


def load(f):
    d = pd.read_csv(f, low_memory=False).sort_values("timestamp", kind="stable").reset_index(drop=True)
    d["act"] = activity(f)
    d["ho"] = as_bool(d["handover_detected"])
    d["pp"] = as_bool(d["ping_pong_detected"])
    d["stale"] = d["cell_info_age_ms"].to_numpy() > STALE_MS
    return d


# ── 1·2. 정합성 / 개수 ──────────────────────────────────────────────────────

def integrity(d):
    cid = d["serving_cell_id"].astype("int64")
    prev = cid.shift()
    ho = d["ho"]
    t = d["timestamp"].to_numpy()

    # 핑퐁 재계산 — 앱 로직(checkHandover)과 같게: 30초 안에 떠났던 셀로 돌아오면 핑퐁
    hist, pp = [], np.zeros(len(d), bool)
    for i in np.where(ho)[0]:
        pp[i] = any(c == cid[i] and t[i] - ts < PP_S * 1000 for c, ts in hist)
        hist.insert(0, (cid[i - 1], t[i]))
        hist = [h for h in hist if t[i] - h[1] <= PP_S * 1000]

    k = d[["serving_cell_id", "rsrp_dbm", "rsrq_db", "sinr_snr_db"]]
    frozen = (k.eq(k.shift()) | (k.isna() & k.shift().isna())).all(axis=1)
    m = d.groupby("serving_cell_id")[["serving_pci", "serving_freq_arfcn"]].nunique()
    rtt = pd.to_numeric(d["rtt_ms"], errors="coerce") if "rtt_ms" in d else pd.Series(np.nan, index=d.index)
    return dict(
        rows=len(d),
        ho=int(ho.sum()),
        cell_changes=int(((cid != prev) & prev.notna()).sum()),
        pp=int(d["pp"].sum()),
        pp_recalc=int(pp.sum()),
        pp_mismatch=int((pp != d["pp"].to_numpy()).sum()),
        prev_cell_mismatch=int((ho & (pd.to_numeric(d["prev_serving_cell_id"], errors="coerce") != prev)).sum()),
        ho_by_event=int((ho & d["collect_trigger"].eq("handover")).sum()),
        ho_by_periodic=int((ho & d["collect_trigger"].eq("periodic")).sum()),
        event_without_ho=int((~ho & d["collect_trigger"].eq("handover")).sum()),
        cell_multi_pci=int((m["serving_pci"] > 1).sum()),
        max_gap_s=round(float(np.diff(t).max() / 1000), 1),
        rsrp_blank=int(d["rsrp_dbm"].isna().sum()),
        stale_rows=int(d["stale"].sum()),
        frozen_rows=int(frozen.sum()),
        weak105=int((d["rsrp_dbm"] <= WEAK[0]).sum()),
        weak110=int((d["rsrp_dbm"] <= WEAK[1]).sum()),
        rtt_blank=int(rtt.isna().sum()),
        rtt_probe_off=bool(rtt.isna().all()),
        rtt_over_1s=int((rtt > 1000).sum()),
        gps_frozen=int(d["gps_speed_ms"].isna().sum()),
    )


def report_counts():
    """리포트(build_viz keep 구간 적용)가 세는 핸드오버 수"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build_viz as bv
    out = {}
    for s in bv.SESSIONS:
        df = bv.load_frame(s)[0]
        out[os.path.basename(s["path"])] = int(as_bool(df["handover_detected"]).sum())
    return out


# ── 3. 핸드오버 전후 RSRP (대조군 비교) ─────────────────────────────────────

def deltas(d):
    """연속 두 행(둘 다 신선)의 RSRP 차 — 핸드오버 행과 아닌 행"""
    fresh = ~d["stale"] & ~d["stale"].shift(fill_value=True)
    out = pd.DataFrame(dict(
        act=d["act"], ho=d["ho"], pp=d["pp"],
        pre=d["rsrp_dbm"].shift(), dlt=d["rsrp_dbm"] - d["rsrp_dbm"].shift(),
        inter_freq=d["serving_freq_arfcn"] != d["serving_freq_arfcn"].shift(),
    ))
    return out[fresh].iloc[1:]


def delta_table(D):
    D = D.copy()
    D["bin"] = pd.cut(D["pre"], [-140, -105, -95, -85, -75, -30],
                      labels=["≤-105", "-105~-95", "-95~-85", "-85~-75", ">-75"])
    rows = []
    for b, g in D.groupby("bin", observed=True):
        h, c = g[g.ho], g[~g.ho]
        rows.append(dict(bin=str(b), n_ho=len(h), ho_mean=round(h.dlt.mean(), 2),
                         ctrl_mean=round(c.dlt.mean(), 2),
                         net=round(h.dlt.mean() - c.dlt.mean(), 2),
                         ho_gain3=round((h.dlt >= 3).mean(), 3), ho_loss3=round((h.dlt <= -3).mean(), 3)))
    h, c = D[D.ho], D[~D.ho]
    return dict(
        all=dict(n_ho=len(h), ho_mean=round(h.dlt.mean(), 2), ho_median=float(h.dlt.median()),
                 ctrl_mean=round(c.dlt.mean(), 2),
                 ho_gain3=round((h.dlt >= 3).mean(), 3), ho_loss3=round((h.dlt <= -3).mean(), 3)),
        pp=dict(n=int(h.pp.sum()), mean=round(h[h.pp].dlt.mean(), 2), median=float(h[h.pp].dlt.median())),
        non_pp=dict(n=int((~h.pp).sum()), mean=round(h[~h.pp].dlt.mean(), 2)),
        inter_freq=dict(n=int(h.inter_freq.sum()), mean=round(h[h.inter_freq].dlt.mean(), 2)),
        by_pre=rows,
        by_act={a: dict(n=len(g), mean=round(g.dlt.mean(), 2)) for a, g in h.groupby("act")},
    )


# ── 4. 규칙 재생 ──────────────────────────────────────────────────────────

def frames(d):
    """행마다 (시각, 실제 서빙 key, {key: rsrp}, 신선 여부)"""
    out = []
    for r in d.itertuples(index=False):
        m = nbr_map(r.neighbors_json)
        sk = (int(r.serving_freq_arfcn), int(r.serving_pci))
        m[sk] = int(r.rsrp_dbm)
        out.append((r.timestamp / 1000.0, sk, m, not r.stale))
    return out


def replay(fr, hyst=3, ttt=1, a2=None, pp_pen=0, pp_win=PP_S, network=False, intra_only=True):
    """
    hyst  : 이웃이 서빙보다 이만큼(dB) 세야 후보 (A3 offset+hysteresis)
    ttt   : 연속 몇 행(≈5초 단위) 조건이 유지돼야 옮기나
    a2    : 서빙 RSRP가 이 값(dBm) 미만일 때만 핸드오버 고려 — None이면 항상
    pp_pen: 최근 pp_win초 안에 떠났던 셀로 돌아갈 때 추가로 요구하는 dB (핑퐁 벌점)
    network=True면 규칙 대신 실제 망의 선택을 그대로 따라간다 (기준선)
    intra_only: 같은 EARFCN 이웃만 후보로 본다. 망의 주파수 간 이동은 RSRP 비교가 아니라
                대역 우선순위(A2→A4/A5)로 정해져서 RSRP만으로 흉내 내면 엉뚱한 대역으로 옮긴다.
    """
    cur, left, cand, cnt = None, [], None, 0
    n_ho = n_pp = n_forced = 0
    rs, gap, forced_last = [], [], []
    last_known = None
    for t, sk, m, fresh in fr:
        if cur is None:
            cur = sk
        new = None
        if network:
            if sk != cur:
                new = sk
        elif cur not in m:
            new, n_forced = sk, n_forced + 1          # 측정이 끊김 → 망이 고른 셀로 강제
            forced_last.append(last_known)
        elif fresh:
            s = m[cur]
            if a2 is None or s < a2:
                best = max((k for k in m if k != cur and (not intra_only or k[0] == cur[0])),
                           key=m.get, default=None)
                if best is not None:
                    need = hyst + (pp_pen if any(k == best and t - ts < pp_win for k, ts in left) else 0)
                    if m[best] - s >= need:
                        cnt = cnt + 1 if best == cand else 1
                        cand = best
                        if cnt >= ttt:
                            new = best
                    else:
                        cand, cnt = None, 0
            else:
                cand, cnt = None, 0
        if new is not None and new != cur:
            n_ho += 1
            if any(k == new and t - ts < PP_S for k, ts in left):
                n_pp += 1
            left = [(cur, t)] + [(k, ts) for k, ts in left if t - ts < max(PP_S, pp_win)]
            cur, cand, cnt = new, None, 0
        if cur in m:
            last_known = m[cur]
        if fresh and cur in m:
            rs.append(m[cur])
            gap.append(max(v for k, v in m.items() if k[0] == cur[0]) - m[cur])   # 같은 주파수 최강 셀 대비
    rs = np.array(rs, float)
    return dict(ho=n_ho, pp=n_pp, forced=n_forced, rows=len(rs),
                rsrp_sum=float(rs.sum()), weak105=int((rs <= WEAK[0]).sum()), weak110=int((rs <= WEAK[1]).sum()),
                gap_sum=float(np.sum(gap)), forced_last=[v for v in forced_last if v is not None])


POLICIES = {
    "실제 망": dict(network=True),
    "A3 3dB·1행": dict(hyst=3, ttt=1),
    "A3 3dB·2행": dict(hyst=3, ttt=2),
    "A3 6dB·1행": dict(hyst=6, ttt=1),
    "A3 3dB·1행 + 핑퐁벌점 6dB": dict(hyst=3, ttt=1, pp_pen=6),
    "A3 3dB·1행 + A2 -95": dict(hyst=3, ttt=1, a2=-95),
    "A3 3dB·1행 + A2 -90": dict(hyst=3, ttt=1, a2=-90),
    "A3 3dB·2행 + 핑퐁벌점 6dB + A2 -95": dict(hyst=3, ttt=2, pp_pen=6, a2=-95),
    "A3 3dB·1행 + 핑퐁벌점 6dB + A2 -90": dict(hyst=3, ttt=1, pp_pen=6, a2=-90),
}


def simulate(sessions):
    res = {}
    for name, p in POLICIES.items():
        acc = {}
        by_act = {}
        for act, fr in sessions:
            r = replay(fr, **p)
            for k, v in r.items():
                acc[k] = acc.get(k, [] if k == "forced_last" else 0) + v
                if k == "forced_last":
                    continue
                by_act.setdefault(act, {}).setdefault(k, 0)
                by_act[act][k] += v
        res[name] = dict(
            ho=acc["ho"], pp=acc["pp"], forced=acc["forced"], voluntary=acc["ho"] - acc["forced"],
            rsrp_mean=round(acc["rsrp_sum"] / acc["rows"], 2),
            weak105_share=round(acc["weak105"] / acc["rows"], 4),
            weak110_share=round(acc["weak110"] / acc["rows"], 4),
            gap_to_best=round(acc["gap_sum"] / acc["rows"], 2),
            # 강제 이동 직전 가상 셀 RSRP — 약했으면 '늦은 핸드오버', 셌으면 이웃 목록 누락(기록 한계)
            forced_last_median=None if not acc["forced_last"] else float(np.median(acc["forced_last"])),
            forced_last_weak95=None if not acc["forced_last"] else round(float(np.mean(np.array(acc["forced_last"]) < -95)), 3),
            by_act={a: dict(ho=v["ho"], pp=v["pp"], rsrp_mean=round(v["rsrp_sum"] / max(v["rows"], 1), 2))
                    for a, v in by_act.items()},
        )
    return res


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    files = sorted(glob.glob("trackingcsv/new/*.csv"))
    per_file, deltas_all, sessions = {}, [], []
    for f in files:
        d = load(f)
        per_file[os.path.basename(f)] = integrity(d)
        deltas_all.append(deltas(d))
        sessions.append((d["act"].iloc[0], frames(d)))

    first = next(iter(per_file.values()))
    tot = {k: sum(v[k] for v in per_file.values()) for k in first if not isinstance(first[k], bool)}
    tot["max_gap_s"] = max(v["max_gap_s"] for v in per_file.values())
    tot["rtt_probe_off_files"] = [k for k, v in per_file.items() if v["rtt_probe_off"]]

    try:
        rc = report_counts()
        trimmed = {k: per_file[k]["ho"] - n for k, n in rc.items() if per_file[k]["ho"] != n}
    except Exception as e:          # build_viz 의존성이 없으면 건너뛴다
        rc, trimmed = None, {"error": str(e)}

    out = dict(
        integrity=dict(total=tot, per_file=per_file),
        report_vs_raw=dict(report_total=None if rc is None else sum(rc.values()),
                           raw_total=tot["ho"], trimmed_by_keep=trimmed),
        delta=delta_table(pd.concat(deltas_all)),
        policies=simulate(sessions),
    )
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)

    # 콘솔 요약
    t = tot
    print(f"[정합성] 행 {t['rows']}  HO {t['ho']} (셀 변경 {t['cell_changes']})  핑퐁 {t['pp']} (재계산 {t['pp_recalc']}, 불일치 {t['pp_mismatch']})")
    print(f"  prev_cell 불일치 {t['prev_cell_mismatch']}  이벤트 감지 {t['ho_by_event']} / 정기 수집에서 감지 {t['ho_by_periodic']} / HO 없는 이벤트 행 {t['event_without_ho']}")
    print(f"  최대 수집 공백 {t['max_gap_s']}s  RSRP 공백 {t['rsrp_blank']}  동결(stale) {t['stale_rows']}  ≤-110dBm {t['weak110']}  RTT>1s {t['rtt_over_1s']}")
    print(f"  RTT 프로브 꺼진 파일 {t['rtt_probe_off_files']}")
    print(f"[개수] 리포트 {out['report_vs_raw']['report_total']} vs 원본 {t['ho']}  keep 구간으로 잘린 것 {trimmed}")
    dl = out["delta"]
    print(f"[전후 RSRP] HO 평균 {dl['all']['ho_mean']} dB (대조 {dl['all']['ctrl_mean']})  +3dB↑ {dl['all']['ho_gain3']:.0%} / -3dB↓ {dl['all']['ho_loss3']:.0%}")
    for r in dl["by_pre"]:
        print(f"  직전 {r['bin']:>9}: HO {r['ho_mean']:+6.2f}  대조 {r['ctrl_mean']:+6.2f}  순효과 {r['net']:+6.2f}  (n={r['n_ho']})")
    print("[규칙 재생]")
    base = out["policies"]["실제 망"]
    for k, v in out["policies"].items():
        print(f"  {k:34s} HO {v['ho']:5d} ({v['ho'] / base['ho'] - 1:+.0%})  핑퐁 {v['pp']:4d}  강제 {v['forced']:4d}"
              f"  평균RSRP {v['rsrp_mean']:7.2f}  ≤-105 {v['weak105_share']:.2%}  최강셀대비 {v['gap_to_best']:.2f}dB"
              f"  강제직전 {v['forced_last_median']} (<-95 {v['forced_last_weak95']})")
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
