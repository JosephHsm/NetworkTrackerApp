"""intra-eNB 핸드오버 분해(밴드변경 vs 섹터변경) + 같은 eNB 내 같은 셀 왕복(intra-eNB 핑퐁).
인자 없으면 6/7 이후 전 파일 일괄."""
import sys, glob, os
import pandas as pd


def analyze(csv):
    df = pd.read_csv(csv)
    df["ho"] = df["handover_detected"].astype(str).str.lower().eq("true")
    df["enb"] = df["serving_cell_id"] // 256

    # 1) intra-eNB 핸드오버: 밴드(ARFCN) 변경 vs 섹터(PCI) 변경
    band, azim, intra_pp_detail = 0, 0, []
    for i in range(1, len(df)):
        r = df.iloc[i]
        if not (r["ho"] and pd.notna(r["prev_serving_cell_id"])):
            continue
        if int(r["prev_serving_cell_id"]) // 256 != r["enb"]:
            continue
        p = df.iloc[i - 1]
        if p["serving_freq_arfcn"] != r["serving_freq_arfcn"]:
            band += 1
        else:
            azim += 1

    # 2) 같은 eNB 안에서 같은 셀로 30초내 복귀
    hist, last, intra_pp = {}, None, 0
    for i in range(len(df)):
        r = df.iloc[i]; cell = int(r["serving_cell_id"]); ts = float(r["timestamp"])
        if last is not None and cell != last:
            if cell in hist and (ts - hist[cell]) < 30000 and (cell // 256) == (last // 256):
                intra_pp += 1
                intra_pp_detail.append(f"{last}->{cell}(eNB {cell//256}, {(ts-hist[cell])/1000:.0f}s)")
            hist[last] = ts
        last = cell

    intra_tot = band + azim
    return {
        "file": os.path.basename(csv).replace("network_log_", "").replace(".csv", ""),
        "intra": intra_tot, "band": band, "azim": azim,
        "band_pct": f"{band/intra_tot*100:.0f}%" if intra_tot else "-",
        "intra_pp": intra_pp, "pp_detail": intra_pp_detail,
    }


files = sys.argv[1:] or sorted(glob.glob("trackingcsv/network_log_2026060[789]*.csv") +
                               glob.glob("trackingcsv/network_log_2026061*.csv"))
res = [analyze(f) for f in files]

print("intra-eNB 핸드오버 분해 + 같은 eNB 내 같은 셀 왕복\n")
print(f"{'파일':40s} {'intra합':>6} {'밴드변경':>7} {'섹터변경':>7} {'밴드%':>6} {'같은셀왕복':>9}")
for r in res:
    print(f"{r['file']:40s} {r['intra']:6d} {r['band']:7d} {r['azim']:7d} {r['band_pct']:>6} {r['intra_pp']:9d}")
tb = sum(r["band"] for r in res); ta = sum(r["azim"] for r in res); tp = sum(r["intra_pp"] for r in res)
print(f"{'합계':40s} {tb+ta:6d} {tb:7d} {ta:7d} {tb/(tb+ta)*100:5.0f}% {tp:9d}")

print("\n같은 eNB 내 같은 셀 왕복 상세:")
for r in res:
    if r["pp_detail"]:
        print(f"  [{r['file']}]")
        for d in r["pp_detail"]:
            print(f"      {d}")
