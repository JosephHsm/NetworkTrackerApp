"""
이웃 셀 ID(ECI) 추정 — 폰이 알려주지 않는 이웃 셀의 셀 ID를 우리 측정으로 메운다

폰은 접속한 셀만 셀 ID를 알려주고, 이웃 셀은 PCI·주파수(EARFCN)·신호만 준다
(neighbors_json 74,415건 전부 cell_id·tac 비어 있음, 2026-10-08 확인). 그런데 우리가 접속했던 셀은
셀 ID·PCI·EARFCN·위치를 다 안다. PCI는 504개를 지역마다 재사용하지만 반경을 좁히면 겹치지 않으므로,
이웃이 "PCI X, EARFCN Y"로 보인 자리에서 R 안에 같은 PCI·EARFCN으로 접속했던 셀이 있으면 그 셀로 본다.

판정
  unique  : R 안에 후보가 하나 → 그 셀
  nearest : 후보가 여럿이지만 가장 가까운 것이 두 번째보다 2배 이상 가깝다 → 가장 가까운 셀
  (그 밖) : 추정하지 않는다

검증: 핸드오버 직전에 이웃이던 셀은 다음 순간 접속 셀이 되어 진짜 셀 ID가 드러난다.
그 세션을 뺀 나머지 세션으로만 추정해 맞는지 센다(leave-one-session-out).

OpenCellID(opencellid.org)도 조사했다: 한국(MCC 450) 파일에 LG U+ LTE는 893개뿐이고 PCI(unit) 칸이 모두 0,
우리가 접속한 셀 중 등록된 건 1%뿐이라 이웃 추정에는 못 쓴다 — 결과는 sector_rules와 함께 리포트 그림 17에 남긴다.

사용법:
    python analysis/neighbor_eci.py
출력: analysis_output/stats/neighbor_eci.json (검증·범위), neighbor_eci.csv (이웃 관측마다 추정 셀 ID)
"""
import gzip, json, os
from collections import defaultdict
import numpy as np
import pandas as pd

OUTDIR = "analysis_output/stats"
OCID_DB = "reference/opencellid/450.csv.gz"   # 내려받은 OpenCellID 한국 파일 (git에 올리지 않는다)
RADIUS_M = 2000          # 이 안의 같은 PCI·EARFCN 접속 셀만 후보로 본다
NEAREST_RATIO = 2.0      # 후보가 여럿일 때 1등이 2등보다 이만큼 가까워야 1등으로 정한다
GRID_DEG = 0.001         # 같은 셀의 접속 위치를 이 격자(약 100 m)로 묶어 후보 계산을 가볍게 한다


def dist_m(lat1, lon1, lat2, lon2):
    return np.hypot((lat1 - lat2) * 111_000, (lon1 - lon2) * 88_000)


def parse(js):
    try:
        a = json.loads(js)
        return [x for x in a if isinstance(x, dict) and x.get("pci") is not None and x.get("earfcn") is not None]
    except Exception:
        return []


def strongest_same_freq(js, s_pci, s_arf):
    """같은 주파수 최강 이웃의 (pci, earfcn, rsrp). 없으면 None."""
    best = None
    for x in parse(js):
        if x.get("rsrp") is None or x["earfcn"] != s_arf or x["pci"] == s_pci:
            continue
        if best is None or x["rsrp"] > best[2]:
            best = (x["pci"], x["earfcn"], x["rsrp"])
    return best


class Index:
    """(PCI, EARFCN) → 그 조합으로 접속했던 셀과 위치들."""

    def __init__(self, obs):
        """obs: (sess, eci, pci, earfcn, lat, lon) 반복자 — 위치가 있는 접속 기록"""
        cells = defaultdict(set)            # (pci, arf) → {(eci, glat, glon, sess)}
        for sess, eci, pci, arf, lat, lon in obs:
            if any(pd.isna(v) for v in (eci, pci, arf, lat, lon)):
                continue
            cells[(int(pci), int(arf))].add((int(eci), round(lat / GRID_DEG) * GRID_DEG,
                                             round(lon / GRID_DEG) * GRID_DEG, sess))
        self.cells = {k: list(v) for k, v in cells.items()}

    def infer(self, pci, arf, lat, lon, exclude=None):
        """→ (eci, 'unique'|'nearest', 거리 m) 또는 None"""
        if any(pd.isna(v) for v in (pci, arf, lat, lon)):
            return None
        best = {}
        for eci, la, lo, sess in self.cells.get((int(pci), int(arf)), ()):
            if sess == exclude:
                continue
            d = dist_m(lat, lon, la, lo)
            if d <= RADIUS_M and d < best.get(eci, np.inf):
                best[eci] = d
        if not best:
            return None
        ranked = sorted(best.items(), key=lambda kv: kv[1])
        if len(ranked) == 1:
            return ranked[0][0], "unique", ranked[0][1]
        if ranked[1][1] >= NEAREST_RATIO * max(ranked[0][1], 50):
            return ranked[0][0], "nearest", ranked[0][1]
        return None


def opencellid_check(our_cells):
    """내려받은 OpenCellID 한국 파일로 우리 셀이 얼마나 등록돼 있는지, PCI 칸이 쓸 만한지."""
    if not os.path.exists(OCID_DB):
        return None
    cols = ["radio", "mcc", "net", "area", "cell", "unit", "lon", "lat", "range", "samples",
            "changeable", "created", "updated", "averageSignal"]
    with gzip.open(OCID_DB, "rt") as f:
        d = pd.read_csv(f, header=None, names=cols, low_memory=False)
    d = d[d.radio != "radio"]
    lte = d[(d.radio == "LTE") & (pd.to_numeric(d.net, errors="coerce") == 6)]
    have = set(pd.to_numeric(lte.cell, errors="coerce").dropna().astype(int))
    unit = pd.to_numeric(lte.unit, errors="coerce")
    return dict(kr_cells=int(len(d)), lgu_lte=int(len(lte)), pci_filled=int((unit > 0).sum()),
                our_cells=len(our_cells), our_found=len(our_cells & have),
                our_enb=len({c // 256 for c in our_cells}),
                our_enb_found=len({c // 256 for c in our_cells} & {c // 256 for c in have}))


def main():
    import build_viz as bv
    os.makedirs(OUTDIR, exist_ok=True)
    frames = []
    for s in bv.SESSIONS:
        df, use_mm, *_ = bv.load_frame(s)
        lat, lon, ok, _, _ = bv.session_coords(df, use_mm)
        frames.append((os.path.basename(s["path"]), df, lat.where(ok), lon.where(ok)))

    obs = []
    for sess, df, lat, lon in frames:
        obs += zip([sess] * len(df), pd.to_numeric(df.serving_cell_id, errors="coerce"),
                   pd.to_numeric(df.serving_pci, errors="coerce"), pd.to_numeric(df.serving_freq_arfcn, errors="coerce"),
                   lat, lon)
    idx = Index(obs)

    # 1) 이웃 관측마다 추정 (전 세션 기준) → CSV
    rows, conf_n = [], defaultdict(int)
    for sess, df, lat, lon in frames:
        t0 = df.timestamp.iloc[0]
        for i, js in enumerate(df.neighbors_json):
            for x in parse(js) if isinstance(js, str) else []:
                if x.get("type") not in (None, "LTE"):
                    continue
                res = idx.infer(x["pci"], x["earfcn"], lat[i], lon[i])
                conf_n["none" if res is None else res[1]] += 1
                rows.append(dict(session=sess, t_s=round((df.timestamp[i] - t0) / 1000, 1),
                                 serving_cell_id=df.serving_cell_id[i], pci=x["pci"], earfcn=x["earfcn"],
                                 rsrp=x.get("rsrp"), cell_id_est=None if res is None else res[0],
                                 enb_est=None if res is None else res[0] // 256,
                                 confidence="" if res is None else res[1],
                                 dist_m=None if res is None else round(res[2])))
    pd.DataFrame(rows).to_csv(os.path.join(OUTDIR, "neighbor_eci.csv"), index=False, encoding="utf-8-sig")

    # 2) 검증: 핸드오버 직전의 이웃 = 다음 접속 셀. 그 세션을 빼고 추정해 맞는지
    val = defaultdict(int)
    for sess, df, lat, lon in frames:
        cell = pd.to_numeric(df.serving_cell_id, errors="coerce")
        pci = pd.to_numeric(df.serving_pci, errors="coerce")
        arf = pd.to_numeric(df.serving_freq_arfcn, errors="coerce")
        for i in range(1, len(df)):
            if pd.isna(cell[i]) or pd.isna(cell[i - 1]) or cell[i] == cell[i - 1]:
                continue
            res = idx.infer(pci[i], arf[i], lat[i - 1], lon[i - 1], exclude=sess)
            if res is None:
                val["none"] += 1
            else:
                val[res[1] + ("_ok" if res[0] == int(cell[i]) else "_wrong")] += 1
    est = sum(v for k, v in val.items() if k != "none")
    ok = val["unique_ok"] + val["nearest_ok"]
    total_nb = sum(conf_n.values())
    our_cells = {int(c) for _, df, _, _ in frames for c in pd.to_numeric(df.serving_cell_id, errors="coerce").dropna()}
    out = dict(
        generated="analysis/neighbor_eci.py", radius_m=RADIUS_M, nearest_ratio=NEAREST_RATIO,
        neighbor_obs=total_nb, est_unique=conf_n["unique"], est_nearest=conf_n["nearest"],
        est_share=round(100 * (conf_n["unique"] + conf_n["nearest"]) / max(total_nb, 1), 1),
        val_handovers=sum(val.values()), val_estimated=est, val_correct=ok,
        val_accuracy=round(100 * ok / max(est, 1), 1), val_detail=dict(val),
        opencellid=opencellid_check(our_cells),
    )
    with open(os.path.join(OUTDIR, "neighbor_eci.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print(f"이웃 관측 {total_nb}건 중 셀 ID 추정 {out['est_share']}% (하나뿐 {conf_n['unique']} / 가장 가까움 {conf_n['nearest']})")
    print(f"검증: 핸드오버 {out['val_handovers']}건 중 추정 {est}건, 정답 {ok}건 ({out['val_accuracy']}%) {dict(val)}")
    print("OpenCellID:", out["opencellid"])


if __name__ == "__main__":
    main()
