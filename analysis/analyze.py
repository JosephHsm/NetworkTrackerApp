"""
NetworkTrackerApp 분석 스크립트
- 상관관계 분석 (correlation)
- CI 패턴 (LTE CI = eNB<<8 | sector 분해, intra/inter 핸드오버)
- 이미지화 (matplotlib PNG)
- 경로 지도 (folium, RSRP 색칠)

사용법:
    python analysis/analyze.py trackingcsv/network_log_20260609_164800_car.csv
    python analysis/analyze.py            # 인자 없으면 6/7 이후 전 파일 일괄
출력: analysis_output/
"""
import sys, os, glob
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import folium

plt.rcParams["font.family"] = "Malgun Gothic"   # 한글 깨짐 방지(Windows)
plt.rcParams["axes.unicode_minus"] = False

OUTDIR = "analysis_output"
os.makedirs(OUTDIR, exist_ok=True)


def rsrp_color(v):
    """RSRP -110(빨강) ~ -60(초록) 색상."""
    if pd.isna(v):
        return "#808080"
    x = max(0.0, min(1.0, (float(v) + 110) / 50))
    r = int(255 * (1 - x)); g = int(180 * x)
    return f"#{r:02x}{g:02x}20"


def analyze(csv):
    name = os.path.splitext(os.path.basename(csv))[0]
    df = pd.read_csv(csv)

    for c in ["handover_detected", "ping_pong_detected"]:
        df[c] = df[c].astype(str).str.lower().eq("true")
    df["t"] = pd.to_datetime(df["datetime"])
    df["gps_held"] = df["gps_speed_ms"].isna()          # 새 fix 없음(홀딩)

    # ── 1. CI 패턴: eNB(>>8) + 섹터(&0xFF) ──────────────────────
    df["enb_id"] = (df["serving_cell_id"] // 256).astype("Int64")
    df["sector"] = (df["serving_cell_id"] % 256).astype("Int64")
    ho = df[df["handover_detected"] & df["prev_serving_cell_id"].notna()].copy()
    ho["prev_enb"] = (ho["prev_serving_cell_id"] // 256).astype("Int64")
    intra = int((ho["prev_enb"] == ho["enb_id"]).sum())
    inter = int((ho["prev_enb"] != ho["enb_id"]).sum())

    # ── 2. 상관관계 ────────────────────────────────────────────
    num_cols = ["rsrp_dbm", "rsrq_db", "rssi_dbm", "sinr_snr_db", "signal_level",
                "mobile_rx_bitrate_Mbps", "neighbor_count", "best_nbr_rsrp_dbm"]
    num = df[num_cols].apply(pd.to_numeric, errors="coerce")
    corr = num.corr()

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(corr, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(len(num_cols))); ax.set_xticklabels(num_cols, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(num_cols))); ax.set_yticklabels(num_cols, fontsize=8)
    for i in range(len(num_cols)):
        for j in range(len(num_cols)):
            v = corr.iloc[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7,
                        color="white" if abs(v) > 0.5 else "black")
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax.set_title(f"상관행렬 — {name}")
    fig.tight_layout(); fig.savefig(f"{OUTDIR}/{name}_corr.png", dpi=130); plt.close(fig)

    # 핵심 대비: RSRP-SINR(진짜) vs RSRP-Rx(가짜)
    r_sinr = num["rsrp_dbm"].corr(num["sinr_snr_db"])
    r_rx = num["rsrp_dbm"].corr(num["mobile_rx_bitrate_Mbps"])
    s = df.dropna(subset=["sinr_snr_db"])
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    axes[0].scatter(s["rsrp_dbm"], s["sinr_snr_db"], s=10, alpha=0.5, color="tab:blue")
    axes[0].set_xlabel("RSRP (dBm)"); axes[0].set_ylabel("SINR (dB)")
    axes[0].set_title(f"RSRP vs SINR  (r={r_sinr:.2f}, 실제 상관)")
    axes[1].scatter(df["rsrp_dbm"], df["mobile_rx_bitrate_Mbps"], s=10, alpha=0.5, color="tab:red")
    axes[1].set_xlabel("RSRP (dBm)"); axes[1].set_ylabel("셀룰러 Rx (Mbps)")
    axes[1].set_title(f"RSRP vs 데이터레이트  (r={r_rx:.2f}, 무상관)")
    fig.suptitle(f"신호-품질은 상관 / 신호-처리량은 무상관 — {name}")
    fig.tight_layout(); fig.savefig(f"{OUTDIR}/{name}_scatter.png", dpi=130); plt.close(fig)

    # ── 3. 시계열: RSRP + 데이터레이트 + 핸드오버/핑퐁 ──────────
    fig, ax1 = plt.subplots(figsize=(12, 4.5))
    ax1.plot(df["t"], df["rsrp_dbm"], color="tab:blue", lw=1)
    ax1.set_ylabel("RSRP (dBm)", color="tab:blue"); ax1.tick_params(axis="y", labelcolor="tab:blue")
    for tt in ho["t"]:
        ax1.axvline(tt, color="gray", alpha=0.25, lw=0.6)
    for tt in df.loc[df["ping_pong_detected"], "t"]:
        ax1.axvline(tt, color="orange", alpha=0.7, lw=1.0)
    ax2 = ax1.twinx()
    ax2.plot(df["t"], df["mobile_rx_bitrate_Mbps"], color="tab:red", lw=0.9, alpha=0.7)
    ax2.set_ylabel("셀룰러 Rx (Mbps)", color="tab:red"); ax2.tick_params(axis="y", labelcolor="tab:red")
    ax1.set_title(f"시간축: RSRP(파랑)·데이터레이트(빨강)·핸드오버(회색)/핑퐁(주황) — {name}")
    fig.tight_layout(); fig.savefig(f"{OUTDIR}/{name}_timeline.png", dpi=130); plt.close(fig)

    # ── 4. 경로 지도 (folium, 동결행 제외) ─────────────────────
    g = df[~df["gps_held"] & df["latitude"].notna()].copy()
    map_pts = len(g)
    if map_pts >= 2:
        m = folium.Map(location=[g["latitude"].mean(), g["longitude"].mean()],
                       zoom_start=14, tiles="cartodbpositron")
        folium.PolyLine(list(zip(g["latitude"], g["longitude"])),
                        color="#3388ff", weight=2, opacity=0.4).add_to(m)
        for _, row in g.iterrows():
            folium.CircleMarker(
                [row["latitude"], row["longitude"]], radius=4,
                color=rsrp_color(row["rsrp_dbm"]), fill=True, fill_opacity=0.85,
                popup=f"RSRP {row['rsrp_dbm']}dBm / SINR {row['sinr_snr_db']} / "
                      f"cell {row['serving_cell_id']} (eNB {row['enb_id']})"
            ).add_to(m)
        # 핸드오버 지점 강조
        for _, row in g[g["handover_detected"]].iterrows():
            folium.CircleMarker([row["latitude"], row["longitude"]], radius=7,
                                color="black", weight=1, fill=False).add_to(m)
        m.save(f"{OUTDIR}/{name}_map.html")

    return {
        "file": name, "rows": len(df), "cells": df["serving_cell_id"].nunique(),
        "enbs": int(df["enb_id"].nunique()), "ho": len(ho), "intra": intra, "inter": inter,
        "pingpong": int(df["ping_pong_detected"].sum()),
        "gps_held": int(df["gps_held"].sum()), "map_pts": map_pts,
        "r_rsrp_sinr": round(float(r_sinr), 2), "r_rsrp_rx": round(float(r_rx), 2),
    }


def main():
    files = sys.argv[1:] or sorted(glob.glob("trackingcsv/network_log_2026060[789]*.csv") +
                                   glob.glob("trackingcsv/network_log_2026061*.csv"))
    for csv in files:
        r = analyze(csv)
        print(f"{r['file']:42s} rows={r['rows']:4d} cells={r['cells']:3d} eNB={r['enbs']:3d} "
              f"HO={r['ho']:3d}(intra {r['intra']}/inter {r['inter']}) PP={r['pingpong']:2d} "
              f"held={r['gps_held']:3d} | r(RSRP,SINR)={r['r_rsrp_sinr']:+.2f} r(RSRP,Rx)={r['r_rsrp_rx']:+.2f}")


if __name__ == "__main__":
    main()
