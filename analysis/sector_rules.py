"""
섹터 이동 기준 조사 — 같은 기지국 안에서 섹터(안테나 방향)가 바뀐 것과 주파수만 바뀐 것을 가를 수 있는가
(2026-10-08 교수님 피드백 "섹터 이동 기준 조사")

기지국은 지금까지 ECI // 256(eNB ID)으로 갈랐다. ECI = eNB ID(20비트) × 256 + 셀 번호(8비트, LCID)라
같은 기지국 안의 셀은 LCID만 다르다. 그런데 LCID 하나가 '섹터 하나'는 아니다 — 한 섹터 안테나가
주파수(캐리어)마다 셀을 따로 연다. 그래서 이번에 본 것은 "LCID 중 어느 부분이 방향이고 어느 부분이 주파수인가"다.

확인한 단서 두 가지 (서로 독립)
  1. LCID 블록: LCID를 6개씩 끊으면 블록마다 주파수가 정해져 있다
       0~5  → EARFCN 3050 (B7 2.6 GHz)
       6~11 → EARFCN 2600 (B5 850 MHz)
       12~17 → EARFCN 100  (B1 2.1 GHz)
     → 섹터 번호 = LCID % 6, 캐리어 = LCID // 6 으로 읽힌다 (18 이상은 추가 셀로 규칙이 약하다).
  2. PCI: 같은 기지국에서 주파수가 다른 두 셀이 PCI가 같으면 같은 방향 안테나다
     (사업자가 같은 섹터의 캐리어들에 같은 PCI를 준다).
  둘이 얼마나 맞물리는지 세어 본다 — 맞물리면 'PCI가 같다 = 같은 섹터'를 기준으로 쓸 수 있다.

핸드오버 분류 (직전 행 → 이 행, 셀 ID가 바뀐 곳)
  inter         다른 기지국 (eNB가 바뀜)
  carrier       같은 기지국 · 같은 섹터(PCI 같음) · 주파수만 바뀜
  sector        같은 기지국 · 같은 주파수 · 섹터(PCI)가 바뀜
  sector_carrier 같은 기지국 · 섹터와 주파수가 함께 바뀜
종류마다 TA(거리) 변화, RSRP·SINR 변화, 이동 속도, 핑퐁 비율을 낸다 — 같은 섹터 안의 주파수 이동이면
안테나가 같으니 TA가 그대로여야 하고, 섹터가 바뀌면 방향이 바뀌니 신호가 달라져야 한다.

사용법:
    python analysis/sector_rules.py
출력: analysis_output/stats/sector_rules.json, sector_handovers.csv (+ 콘솔 요약)
"""
import glob, itertools, json, os
import numpy as np
import pandas as pd

OUTDIR = "analysis_output/stats"
BAND = {100: "B1 2.1GHz", 2600: "B5 850MHz", 3050: "B7 2.6GHz"}
BLOCK_ARFCN = {0: 3050, 1: 2600, 2: 100}       # LCID // 6 → 주로 쓰이는 EARFCN
KINDS = ["inter", "carrier", "sector", "sector_carrier"]
KIND_KO = {"inter": "다른 기지국", "carrier": "같은 섹터 · 주파수만", "sector": "섹터만 (같은 주파수)",
           "sector_carrier": "섹터 + 주파수"}


def activity(path):
    for a in ("subway", "car", "walking", "home"):
        if path.endswith(f"_{a}.csv"):
            return a
    return "car"      # 8월 _unknown 두 건은 차량 (build_viz.py 근거)


def num(d, c):
    return pd.to_numeric(d[c], errors="coerce").to_numpy() if c in d.columns else np.full(len(d), np.nan)


def load():
    rows, hos = [], []
    for f in sorted(glob.glob("trackingcsv/new/*.csv")):
        d = pd.read_csv(f, low_memory=False).sort_values("timestamp").reset_index(drop=True)
        act = activity(f)
        cell, pci, arf = num(d, "serving_cell_id"), num(d, "serving_pci"), num(d, "serving_freq_arfcn")
        ta, rsrp, sinr = num(d, "timing_advance_lte"), num(d, "rsrp_dbm"), num(d, "sinr_snr_db")
        spd = num(d, "gps_speed_ms")
        pp = d["ping_pong_detected"].astype(str).str.lower().eq("true").to_numpy() \
            if "ping_pong_detected" in d.columns else np.zeros(len(d), bool)
        t = d["timestamp"].to_numpy() / 1000.0
        ok = ~np.isnan(cell)
        rows.append(pd.DataFrame(dict(cell=cell[ok], pci=pci[ok], arf=arf[ok])))
        idx = np.where(ok)[0]
        for a, b in zip(idx[:-1], idx[1:]):
            if cell[a] == cell[b]:
                continue
            e0, e1 = int(cell[a]) // 256, int(cell[b]) // 256
            same_pci, same_arf = pci[a] == pci[b], arf[a] == arf[b]
            if e0 != e1:
                k = "inter"
            elif same_arf:
                k = "sector"
            elif same_pci:
                k = "carrier"
            else:
                k = "sector_carrier"
            hos.append(dict(
                file=os.path.basename(f), activity=act, t=round(t[b] - t[0], 1), gap_s=round(t[b] - t[a], 1),
                from_cell=int(cell[a]), to_cell=int(cell[b]), enb_from=e0, enb_to=e1,
                lcid_from=int(cell[a]) % 256, lcid_to=int(cell[b]) % 256,
                pci_from=pci[a], pci_to=pci[b], arf_from=arf[a], arf_to=arf[b], kind=k,
                same_pci=bool(same_pci), same_lcid_mod6=(int(cell[a]) % 256) % 6 == (int(cell[b]) % 256) % 6,
                ta_from=ta[a], ta_to=ta[b], rsrp_from=rsrp[a], rsrp_to=rsrp[b],
                sinr_from=sinr[a], sinr_to=sinr[b], speed=spd[b], pp=bool(pp[b]),
            ))
    cells = pd.concat(rows).drop_duplicates("cell").astype(float)
    return cells, pd.DataFrame(hos)


def med(s):
    s = pd.Series(s).dropna()
    return None if s.empty else round(float(s.median()), 1)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    cells, ho = load()
    cells["enb"] = (cells.cell // 256).astype(int)
    cells["lcid"] = (cells.cell % 256).astype(int)

    # 1. LCID 블록 → 주파수
    blk = []
    for b, a in BLOCK_ARFCN.items():
        m = cells.lcid.between(b * 6, b * 6 + 5)
        blk.append(dict(lcid=f"{b * 6}~{b * 6 + 5}", arfcn=a, band=BAND[a], cells=int(m.sum()),
                        match=round(100 * float((cells.arf[m] == a).mean()), 1)))
    extra = int((cells.lcid >= 18).sum())

    # 2. 같은 기지국, 주파수가 다른 셀 쌍: PCI 일치 ↔ LCID%6 일치
    pairs = []
    for _, g in cells.groupby("enb"):
        for a, b in itertools.combinations(g.itertuples(), 2):
            if a.arf == b.arf or a.lcid >= 18 or b.lcid >= 18:
                continue
            pairs.append((a.pci == b.pci, a.lcid % 6 == b.lcid % 6))
    pairs = pd.DataFrame(pairs, columns=["same_pci", "same_mod6"])
    both = int((pairs.same_pci & pairs.same_mod6).sum())
    agree = dict(
        pairs=len(pairs),
        same_pci=int(pairs.same_pci.sum()), same_mod6=int(pairs.same_mod6.sum()), both=both,
        pci_then_mod6=round(100 * both / max(pairs.same_pci.sum(), 1), 1),
        mod6_then_pci=round(100 * both / max(pairs.same_mod6.sum(), 1), 1),
    )
    # 같은 주파수 안에서 PCI가 겹치는 셀(같은 기지국) — 있으면 PCI로 섹터를 못 가른다
    dup = int(sum(g.groupby("arf").pci.apply(lambda s: s.duplicated().sum()).sum() for _, g in cells.groupby("enb")))

    # 리포트 그림 17의 예시 기지국: 섹터 번호(LCID%6)가 같은 셀끼리 PCI가 같은 열이 가장 많은 곳
    def score(g):
        g = g[g.lcid < 18]
        cols = [c for _, c in g.groupby(g.lcid % 6) if len(c) >= 2]
        return (sum(c.pci.nunique() == 1 for c in cols), len(g))
    ex_enb = max(cells.groupby("enb"), key=lambda kv: score(kv[1]))[0]
    eg = cells[(cells.enb == ex_enb) & (cells.lcid < 18)].sort_values("lcid")
    example = dict(enb=int(ex_enb), cells=[[int(r.lcid), int(r.pci), int(r.arf)] for r in eg.itertuples()],
                   pcis=sorted({int(v) for v in eg.pci}))

    # 3. 핸드오버 종류별 특징
    ho["dta"] = (ho.ta_to - ho.ta_from).abs()
    ho["drsrp"] = ho.rsrp_to - ho.rsrp_from
    ho["dsinr"] = ho.sinr_to - ho.sinr_from
    by = []
    for k in KINDS:
        g = ho[ho.kind == k]
        ta_ok = g.dta.notna()
        by.append(dict(
            kind=k, ko=KIND_KO[k], n=len(g), share=round(100 * len(g) / max(len(ho), 1), 1),
            ta_changed=None if not ta_ok.any() else round(100 * float((g.dta[ta_ok] > 0).mean()), 1),
            ta_ge2=None if not ta_ok.any() else round(100 * float((g.dta[ta_ok] >= 2).mean()), 1),
            drsrp=med(g.drsrp), abs_drsrp=med(g.drsrp.abs()), dsinr=med(g.dsinr), abs_dsinr=med(g.dsinr.abs()),
            n_sinr=int(g.dsinr.notna().sum()),
            speed_kmh=med(g.speed * 3.6), pp=round(100 * float(g.pp.mean()), 1) if len(g) else None,
            mod6_same=round(100 * float(g.same_lcid_mod6.mean()), 1) if len(g) else None,
        ))
    # 다른 기지국인데 PCI가 같다 → 같은 자리의 다른 eNB일 가능성
    inter_same_pci = int((ho.kind.eq("inter") & ho.same_pci).sum())
    by_act = ho.groupby(["activity", "kind"]).size().unstack(fill_value=0).reindex(columns=KINDS, fill_value=0)

    out = dict(
        generated="analysis/sector_rules.py", n_cells=len(cells), n_enb=int(cells.enb.nunique()),
        lcid_blocks=blk, lcid_extra=extra, example=example, pci_lcid_agree=agree, pci_dup_same_arf=dup,
        handover_kinds=by, n_handovers=len(ho), inter_same_pci=inter_same_pci,
        by_activity={a: {k: int(v) for k, v in r.items()} for a, r in by_act.iterrows()},
    )
    with open(os.path.join(OUTDIR, "sector_rules.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    ho.to_csv(os.path.join(OUTDIR, "sector_handovers.csv"), index=False, encoding="utf-8-sig")

    print(f"셀 {len(cells)}개 · 기지국 {cells.enb.nunique()}곳")
    for b in blk:
        print(f"  LCID {b['lcid']:>6} → {b['band']:<10} 셀 {b['cells']:4d}개 중 {b['match']}% 일치")
    print(f"  LCID 18 이상(블록 밖) {extra}개")
    print(f"같은 기지국·다른 주파수 셀 쌍 {agree['pairs']}개: PCI 같음 {agree['same_pci']} / LCID%6 같음 {agree['same_mod6']} / 둘 다 {both}")
    print(f"  PCI 같으면 LCID%6도 같다 {agree['pci_then_mod6']}% · LCID%6 같으면 PCI도 같다 {agree['mod6_then_pci']}%")
    print(f"  같은 기지국·같은 주파수에서 PCI가 겹치는 셀 {dup}개")
    print(f"\n핸드오버 {len(ho)}건 (다른 기지국인데 PCI 같음 {inter_same_pci}건)")
    print(f"  {'종류':<16}{'건수':>6}{'비율':>7}{'TA변화':>8}{'TA≥2':>7}{'ΔRSRP':>7}{'|ΔRSRP|':>8}{'ΔSINR':>7}{'|ΔSINR|':>8}{'속도':>6}{'핑퐁':>6}")
    for r in by:
        print(f"  {r['ko']:<16}{r['n']:6d}{r['share']:6.1f}%{str(r['ta_changed']):>8}{str(r['ta_ge2']):>7}"
              f"{str(r['drsrp']):>7}{str(r['abs_drsrp']):>8}{str(r['dsinr']):>7}{str(r['abs_dsinr']):>8}"
              f"{str(r['speed_kmh']):>6}{str(r['pp']):>6}")
    print(by_act.to_string())


if __name__ == "__main__":
    main()
