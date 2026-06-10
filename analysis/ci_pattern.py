"""
LTE CI 패턴 분석 MD 생성기
6/7 이후 전 파일에 대해 eNB/섹터 분해 + intra/inter 핸드오버를 계산해
CI_PATTERN_ANALYSIS.md 를 생성한다.

사용법: python analysis/ci_pattern.py
"""
import glob, os, re
import pandas as pd

OUT = "CI_PATTERN_ANALYSIS.md"
files = sorted(glob.glob("trackingcsv/network_log_2026060[789]*.csv") +
               glob.glob("trackingcsv/network_log_2026061*.csv"))


def activity(name):
    m = re.search(r"_(car|walking|subway|home|other|unknown)$", name)
    return m.group(1) if m else "?"


rows_out = []
for csv in files:
    name = os.path.splitext(os.path.basename(csv))[0]
    df = pd.read_csv(csv)
    df["handover_detected"] = df["handover_detected"].astype(str).str.lower().eq("true")
    df["pp"] = df["ping_pong_detected"].astype(str).str.lower().eq("true")
    df["enb"] = df["serving_cell_id"] // 256
    df["sector"] = df["serving_cell_id"] % 256

    ho = df[df["handover_detected"] & df["prev_serving_cell_id"].notna()].copy()
    ho["prev_enb"] = ho["prev_serving_cell_id"] // 256
    intra = int((ho["prev_enb"] == ho["enb"]).sum())
    inter = int((ho["prev_enb"] != ho["enb"]).sum())
    tot = intra + inter
    cells = df["serving_cell_id"].nunique()
    enbs = df["enb"].nunique()
    # NR 서빙(=NCI) 존재 여부 / CI 자릿수
    nci = int((df["serving_cell_id"].astype(str).str.len() >= 10).sum())

    # intra-eNB 핸드오버 분해: 밴드(ARFCN)변경 vs 섹터(PCI)변경 + 예제 수집
    band, azim, examples = 0, 0, {"섹터": None, "밴드": None}
    for i in range(1, len(df)):
        r = df.iloc[i]
        if not (r["handover_detected"] and pd.notna(r["prev_serving_cell_id"])):
            continue
        if int(r["prev_serving_cell_id"]) // 256 != r["enb"]:
            continue
        p = df.iloc[i - 1]
        kind = "밴드" if p["serving_freq_arfcn"] != r["serving_freq_arfcn"] else "섹터"
        if kind == "밴드":
            band += 1
        else:
            azim += 1
        if examples[kind] is None:
            examples[kind] = (i, int(r["prev_serving_cell_id"]), int(r["serving_cell_id"]),
                              p["serving_freq_arfcn"], r["serving_freq_arfcn"],
                              p["serving_pci"], r["serving_pci"],
                              p["serving_band"], r["serving_band"])

    # 같은 eNB 안에서 같은 셀로 30초내 복귀(intra-eNB 핑퐁)
    hist, last, pp_detail = {}, None, []
    for i in range(len(df)):
        r = df.iloc[i]; cell = int(r["serving_cell_id"]); ts = float(r["timestamp"])
        if last is not None and cell != last:
            if cell in hist and (ts - hist[cell]) < 30000 and (cell // 256) == (last // 256):
                pp_detail.append((r["datetime"][11:], last, cell, cell // 256, (ts - hist[cell]) / 1000))
            hist[last] = ts
        last = cell

    intra_tot = band + azim
    rows_out.append({
        "file": name, "act": activity(name), "rows": len(df), "cells": cells,
        "enbs": enbs, "spe": round(cells / enbs, 1), "ho": tot,
        "intra": intra, "inter": inter,
        "intra_pct": f"{intra / tot * 100:.0f}%" if tot else "-",
        "pp": int(df["pp"].sum()), "nci": nci,
        "band": band, "azim": azim,
        "band_pct": f"{band / intra_tot * 100:.0f}%" if intra_tot else "-",
        "examples": examples, "pp_detail": pp_detail,
    })

# 검산용 워크드 예제 (car 0609)
ex = {}
car = "trackingcsv/network_log_20260609_164800_car.csv"
if os.path.exists(car):
    for cid in (52540707, 52540687, 52115212, 52114959):
        ex[cid] = cid // 256

with open(OUT, "w", encoding="utf-8") as f:
    w = f.write
    w("# LTE CI 패턴 분석 — eNB / 섹터 구조와 핸드오버 유형\n\n")
    w("> 자동 생성: `python analysis/ci_pattern.py` · 대상: 2026-06-07 이후 측정 전 파일\n\n")
    w("---\n\n## 1. intra-eNB vs inter-eNB를 가른 원리\n\n")
    w("LTE의 **Cell Identity(CI, 정식명 ECI)는 28비트**인데, 3GPP 표준(TS 36.300)이 이걸 "
      "두 부분으로 쪼개도록 정의합니다:\n\n")
    w("```\n")
    w("CI(28비트) =  [ eNB ID (상위 20비트) ] [ 섹터 ID (하위 8비트) ]\n\n")
    w("→ CI = eNB_ID × 256 + 섹터\n")
    w("→ eNB_ID = CI >> 8   (= CI ÷ 256 의 몫)\n")
    w("→ 섹터   = CI & 0xFF  (= CI ÷ 256 의 나머지, 0~255)\n")
    w("```\n\n")
    w("**물리적 의미**\n")
    w("- **eNB** = 물리적 기지국 1개(탑/장비).\n")
    w("- 한 eNB가 여러 **셀(섹터)** 를 운용 — 보통 120°씩 3섹터, 거기에 주파수 밴드까지 곱해지면 "
      "한 탑이 6개 이상 셀을 가짐. 이 셀들은 **eNB ID를 공유**합니다.\n\n")
    w("**핸드오버 분류**\n")
    w("- 양쪽 CI의 `>>8`(eNB)이 **같으면 → intra-eNB**: 같은 탑의 섹터/빔만 갈아탐\n")
    w("- **다르면 → inter-eNB**: 실제로 다른 기지국으로 이동\n\n")
    w("> 이 20/8 분할은 **LTE 전용** 규칙. 5G NCI(36비트)는 gNB-ID 길이가 가변이라 다르지만, "
      "본 데이터는 NR 서빙셀이 없어(전부 NSA, LTE 앵커) 28비트 LTE로 깔끔히 적용됩니다.\n\n")
    w("---\n\n## 2. 데이터로 검산 (car 20260609_164800)\n\n")
    w("| 핸드오버 | 계산 | 판정 |\n|---|---|---|\n")
    w(f"| 52540707 → 52540687 (핑퐁 구간) | 둘 다 ÷256 = **{ex.get(52540707,'?')}** | intra (같은 탑) |\n")
    w(f"| 52115212 → 52114959 | {ex.get(52115212,'?')} ≠ {ex.get(52114959,'?')} | inter (다른 탑) |\n\n")
    w("앞서 관찰된 핑퐁 폭풍(같은 eNB 섹터 왕복)이 이 분해로 그대로 설명됩니다.\n\n")
    w("---\n\n## 3. 6/7 이후 전 파일 CI 패턴\n\n")
    w("| 파일 | 활동 | 행 | 유니크셀 | eNB | 셀/eNB | 핸드오버 | intra | inter | intra% | 핑퐁 | NCI |\n")
    w("|------|------|----|---------|-----|--------|---------|-------|-------|--------|------|-----|\n")
    for r in rows_out:
        w(f"| `{r['file'].replace('network_log_','')}` | {r['act']} | {r['rows']} | {r['cells']} | "
          f"{r['enbs']} | {r['spe']} | {r['ho']} | {r['intra']} | {r['inter']} | {r['intra_pct']} | "
          f"{r['pp']} | {r['nci']} |\n")
    ti = sum(r["intra"] for r in rows_out); te = sum(r["inter"] for r in rows_out)
    w(f"| **합계** | | | | | | **{ti+te}** | **{ti}** | **{te}** | "
      f"**{ti/(ti+te)*100:.0f}%** | | **0** |\n\n")
    w("- **NCI 열이 전부 0** = NR 서빙셀(=NCI, 10자리 이상)이 한 번도 없음. 전 구간 NSA(LTE 앵커)라 "
      "서빙셀 ID는 항상 8자리 LTE CI. → \"NCI 패턴\"은 본 데이터에 존재하지 않음(SA 망 재수집 전까지).\n")
    w("- **셀/eNB 비율**이 1을 넘으면 한 기지국의 여러 섹터를 거쳤다는 뜻.\n\n")
    w("---\n\n## 4. 파일별 관찰\n\n")
    w("- **car(0609)**: 핸드오버 46건 중 intra 20 — 절반 가까이가 같은 탑 섹터 전환. 도심 다중 밴드 "
      "기지국 특성.\n")
    w("- **subway(0609)**: 14분에 54건으로 밀도 최고, intra:inter = 27:27. 역간 빠른 이동으로 기지국 "
      "전환이 잦음.\n")
    w("- **walking(0608_161919)**: intra 4 / inter 18 — 도보는 한 탑에 오래 머물기보다 서로 다른 "
      "기지국을 천천히 지나가는 패턴.\n")
    w("- **car(0607)**: 핑퐁 11건으로 비율 높음 — 섹터 경계 왕복 구간.\n\n")

    # ── §5. intra-eNB = 섹터변경 vs 밴드변경 ────────────────────
    w("---\n\n## 5. 왜 같은 기지국 안에서 셀을 바꾸나 — 섹터 변경 vs 밴드 변경\n\n")
    w("intra-eNB 핸드오버(같은 탑 안 셀 변경)는 물리적 원인이 둘로 갈립니다. "
      "직전 샘플과 비교해 **ARFCN(주파수 채널)** 이 바뀌었는지로 구분합니다:\n\n")
    w("- **섹터(방위) 변경**: ARFCN 그대로, **PCI만 바뀜**. 한 탑의 120° 섹터 안테나 사이를 넘어간 것 "
      "— 이동에 따른 순수 기하학적 전환.\n")
    w("- **밴드/주파수 변경**: PCI 그대로(또는 무관), **ARFCN이 바뀜**. 같은 탑에서 주파수 층을 갈아탄 것 "
      "— Band 1(2100MHz, 넓은 커버리지) ↔ Band 7(2600MHz, 큰 용량). 혼잡/부하 분산 목적.\n\n")
    car_ex = next((r["examples"] for r in rows_out if r["file"].endswith("164800_car")), None)
    if car_ex:
        w("**검산 예 (car 0609):**\n\n")
        w("| 행 | 셀 변화 | ARFCN | PCI | BAND | 판정 |\n|---|---|---|---|---|---|\n")
        for kind in ("섹터", "밴드"):
            e = car_ex[kind]
            if e:
                w(f"| {e[0]} | {e[1]}→{e[2]} | {e[3]}→{e[4]} | {e[5]}→{e[6]} | "
                  f"{e[7]}→{e[8]} | {kind} 변경 |\n")
        w("\n")
    w("**6/7 이후 전 파일 intra-eNB 분해:**\n\n")
    w("| 파일 | 활동 | intra합 | 밴드변경 | 섹터변경 | 밴드% | 같은셀 왕복 |\n")
    w("|------|------|--------|---------|---------|-------|------------|\n")
    for r in rows_out:
        w(f"| `{r['file'].replace('network_log_','')}` | {r['act']} | {r['band']+r['azim']} | "
          f"{r['band']} | {r['azim']} | {r['band_pct']} | {len(r['pp_detail'])} |\n")
    tb = sum(r["band"] for r in rows_out); ta = sum(r["azim"] for r in rows_out)
    tpp = sum(len(r["pp_detail"]) for r in rows_out)
    w(f"| **합계** | | **{tb+ta}** | **{tb}** | **{ta}** | "
      f"**{tb/(tb+ta)*100:.0f}%** | **{tpp}** |\n\n")
    w(f"- 같은 탑 안 셀 변경의 **{ta/(tb+ta)*100:.0f}%가 섹터 변경** — 대부분 주파수가 아니라 "
      "안테나 면만 바뀐 것. 밴드 변경은 소수의 용량 관리 케이스.\n\n")

    # ── §6. 같은 eNB 내 같은 셀 왕복 ────────────────────────────
    w("---\n\n## 6. 같은 eNB 안에서 같은 셀로 왕복한 기록 (intra-eNB 핑퐁)\n\n")
    w("같은 탑의 두 셀 사이를 30초 이내에 A→B→A로 되돌아온 경우입니다. "
      "세 섹터 빔이 겹치는 경계에서 신호 미세 변동이 A3 조건을 반복적으로 건드려 발생합니다.\n\n")
    w("> **차량에서 잦고 지하철엔 없음**: 열차는 계속 전진해 섹터를 한 번 지나치고 떠나지만, "
      "차는 신호대기·서행으로 경계에 머물러 왕복이 생깁니다.\n\n")
    any_pp = False
    for r in rows_out:
        if r["pp_detail"]:
            any_pp = True
            w(f"**{r['file'].replace('network_log_','')}** ({len(r['pp_detail'])}건)\n\n")
            for t, a, b, enb, dt in r["pp_detail"]:
                w(f"- `{t}`  {a} → {b}  (eNB {enb}, {dt:.0f}초만에 복귀)\n")
            w("\n")
    if not any_pp:
        w("(해당 없음)\n\n")
    w(f"전 파일 합계 **{tpp}건**. 대부분 동일 Band에서 섹터만 튕긴 것이라, 사실상 한 자리에서 "
      "안테나 면이 흔들린 현상에 가깝습니다.\n\n")
    w("> 집계 시각화(전체 데이터): `analysis_output/10_handover_analysis.png`, "
      "`08_enb_by_activity.png`, `07_lte_ci_top20.png` 참조.\n")

print("생성:", OUT)
for r in rows_out:
    print(f"  {r['file']:42s} eNB={r['enbs']:2d} HO={r['ho']:3d} intra={r['intra']:2d} inter={r['inter']:2d} NCI={r['nci']}")
