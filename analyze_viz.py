"""
논문용 시각화 — NetworkTrackerApp
생성 파일: viz_01 ~ viz_06 (6장)
"""
import sys, io, json, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.dates as mdates
import seaborn as sns
from pathlib import Path

# ── 경로 ──────────────────────────────────────────────────────────
CSV_DIR = Path(r"C:\Users\admin\NetworkTrackerApp\trackingcsv")
OUT_DIR = Path(r"C:\Users\admin\NetworkTrackerApp\analysis_output")
OUT_DIR.mkdir(exist_ok=True)

# ── 스타일 ─────────────────────────────────────────────────────────
FONT = "Malgun Gothic"
plt.rcParams.update({
    "font.family":        FONT,
    "axes.unicode_minus": False,
    "axes.spines.top":    False,
    "axes.spines.right":  False,
    "axes.grid":          True,
    "grid.alpha":         0.35,
    "grid.linestyle":     "--",
    "figure.dpi":         150,
    "savefig.dpi":        200,
    "savefig.bbox":       "tight",
    "savefig.facecolor":  "white",
})

ACT_COLOR = {
    "home":    "#4C72B0",
    "walking": "#55A868",
    "car":     "#C44E52",
    "subway":  "#8172B2",
    "unknown": "#937860",
}
ACT_KO = {
    "home": "실내(home)", "walking": "도보(walking)",
    "car": "차량(car)", "subway": "지하철(subway)", "unknown": "불명",
}

# ── 데이터 로드 ────────────────────────────────────────────────────
def load_all():
    frames = []
    for f in sorted(CSV_DIR.glob("*.csv")):
        df = pd.read_csv(f, low_memory=False)
        df["_file"]     = f.name
        df["_activity"] = f.name.rsplit("_", 1)[-1].replace(".csv", "")
        df["_datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        if "sinr_db" in df.columns and "sinr_snr_db" not in df.columns:
            df = df.rename(columns={"sinr_db": "sinr_snr_db"})
        frames.append(df)
    return pd.concat(frames, ignore_index=True)

all_df  = load_all()
new_df  = all_df[all_df["_file"].str.contains("2026051[2-9]|202605[2-9]")].copy()
sub_df  = new_df[new_df["_activity"] == "subway"].copy()

# 셀룰러 전용 처리량 컬럼 선택
rx_col = "mobile_rx_bitrate_Mbps" if "mobile_rx_bitrate_Mbps" in new_df.columns else "rx_bitrate_Mbps"

print(f"전체 {len(all_df):,}행 | 최신포맷 {len(new_df):,}행 | 지하철 {len(sub_df):,}행")

# ══════════════════════════════════════════════════════════════════
# VIZ 01 — 활동별 RSRP / SINR 비교 (바이올린 + 박스)
# ══════════════════════════════════════════════════════════════════
print("\n[1/6] 활동별 신호 품질 비교...")

acts = ["home", "walking", "car", "subway"]
fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

for ax, (col, label, refs) in zip(axes, [
    ("rsrp_dbm",   "RSRP (dBm)",  [(-80, "양호 기준", "green"), (-100, "불량 기준", "red")]),
    ("sinr_snr_db","SINR (dB)",   [(10,  "양호 기준", "green"), ( 0,   "불량 기준", "red")]),
]):
    rows = []
    for act in acts:
        vals = pd.to_numeric(new_df[new_df["_activity"] == act][col], errors="coerce").dropna()
        rows.append({"activity": ACT_KO[act], "values": vals, "color": ACT_COLOR[act]})

    # 바이올린
    parts = ax.violinplot(
        [r["values"] for r in rows],
        positions=range(len(rows)),
        widths=0.6, showmedians=False, showextrema=False
    )
    for pc, r in zip(parts["bodies"], rows):
        pc.set_facecolor(r["color"])
        pc.set_alpha(0.35)

    # 박스
    bp = ax.boxplot(
        [r["values"] for r in rows],
        positions=range(len(rows)),
        widths=0.22, patch_artist=True,
        medianprops=dict(color="black", linewidth=2),
        whiskerprops=dict(linewidth=1.2),
        capprops=dict(linewidth=1.2),
        flierprops=dict(marker=".", markersize=2, alpha=0.3),
    )
    for patch, r in zip(bp["boxes"], rows):
        patch.set_facecolor(r["color"])
        patch.set_alpha(0.7)

    # 기준선
    for val, txt, c in refs:
        ax.axhline(val, color=c, linestyle="--", linewidth=1.2, alpha=0.7)
        ax.text(len(rows) - 0.45, val + 0.5, txt, color=c, fontsize=8, va="bottom")

    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([r["activity"] for r in rows], fontsize=10)
    ax.set_ylabel(label, fontsize=11)
    ax.set_title(f"활동 유형별 {label} 분포", fontsize=12, fontweight="bold")

    # 중앙값 텍스트
    for i, r in enumerate(rows):
        med = r["values"].median()
        ax.text(i, med + 0.5, f"{med:.1f}", ha="center", va="bottom",
                fontsize=8, fontweight="bold", color="black")

fig.suptitle("이동 환경에 따른 셀룰러 신호 품질 비교", fontsize=14, fontweight="bold", y=1.01)
plt.tight_layout()
plt.savefig(OUT_DIR / "viz_01_signal_quality_by_activity.png")
plt.close()
print("  → viz_01 저장")

# ══════════════════════════════════════════════════════════════════
# VIZ 02 — 지하철 RSRP 시계열 + 핸드오버 마커
# ══════════════════════════════════════════════════════════════════
print("[2/6] 지하철 RSRP 시계열 + 핸드오버...")

sub_files = sorted(sub_df["_file"].unique())
# 행수가 가장 많은 subway 세션 2개 선택
top2 = sub_df.groupby("_file").size().nlargest(2).index.tolist()

fig, axes = plt.subplots(len(top2), 1, figsize=(14, 4.5 * len(top2)))
if len(top2) == 1:
    axes = [axes]
fig.suptitle("지하철 세션 RSRP 시계열 및 핸드오버 이벤트", fontsize=13, fontweight="bold")

for ax, fname in zip(axes, top2):
    fdf = sub_df[sub_df["_file"] == fname].sort_values("_datetime").reset_index(drop=True)
    t    = fdf["_datetime"]
    rsrp = pd.to_numeric(fdf["rsrp_dbm"], errors="coerce")
    ci   = fdf["serving_cell_id"].astype(str)

    # 5G 구간 배경 (is_5g_display=true)
    is5g = fdf["is_5g_display"].astype(str).str.lower() == "true" if "is_5g_display" in fdf.columns else pd.Series([False] * len(fdf))
    in5g = False
    for i in range(len(fdf)):
        if is5g.iloc[i] and not in5g:
            start5g = t.iloc[i]; in5g = True
        elif not is5g.iloc[i] and in5g:
            ax.axvspan(start5g, t.iloc[i], alpha=0.08, color="purple", label="_nolegend_")
            in5g = False
    if in5g:
        ax.axvspan(start5g, t.iloc[-1], alpha=0.08, color="purple")

    # RSRP 라인
    ax.plot(t, rsrp, linewidth=1.3, color="#4C72B0", zorder=3)
    ax.fill_between(t, rsrp, -130, alpha=0.15, color="#4C72B0")

    # 기준선
    ax.axhline(-80,  color="green", linestyle="--", linewidth=1, alpha=0.7)
    ax.axhline(-100, color="red",   linestyle="--", linewidth=1, alpha=0.7)
    ax.text(t.iloc[0], -79,  "양호(-80)", color="green", fontsize=8)
    ax.text(t.iloc[0], -99,  "불량(-100)", color="red",  fontsize=8)

    # 핸드오버 마커
    ho_mask  = (ci != ci.shift()).fillna(False)
    pp_mask  = fdf["ping_pong_detected"].astype(str).str.lower().eq("true") if "ping_pong_detected" in fdf.columns else pd.Series([False]*len(fdf))

    ho_times  = t[ho_mask & ~pp_mask]
    pp_times  = t[ho_mask & pp_mask]
    ho_rsrp   = rsrp[ho_mask & ~pp_mask]
    pp_rsrp   = rsrp[ho_mask & pp_mask]

    ax.scatter(ho_times, ho_rsrp, color="orange", s=35, zorder=5,
               marker="^", label=f"핸드오버 ({ho_mask.sum()}건)")
    if pp_mask.any():
        ax.scatter(pp_times, pp_rsrp, color="red", s=50, zorder=6,
                   marker="*", label=f"핑퐁 ({pp_mask.sum()}건)")

    ax.set_ylim(-125, -40)
    ax.set_ylabel("RSRP (dBm)", fontsize=10)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=2))
    stamp = fname[12:25]
    ax.set_title(f"세션 {stamp}", fontsize=11)
    ax.legend(loc="upper right", fontsize=9)

    # 5G 범례 패치
    if is5g.any():
        ax.add_patch(mpatches.Patch(facecolor="purple", alpha=0.15, label="5G NSA 구간"))
        ax.legend(loc="upper right", fontsize=9)

plt.tight_layout()
plt.savefig(OUT_DIR / "viz_02_subway_rsrp_timeline.png")
plt.close()
print("  → viz_02 저장")

# ══════════════════════════════════════════════════════════════════
# VIZ 03 — 핸드오버 분석 (활동별 빈도 + 핑퐁 비율)
# ══════════════════════════════════════════════════════════════════
print("[3/6] 핸드오버 분석...")

ho_stats = []
for act in acts:
    adf = new_df[new_df["_activity"] == act].copy()
    if adf.empty:
        continue
    dur_min = adf.groupby("_file")["_datetime"].apply(
        lambda t: (t.max() - t.min()).total_seconds() / 60
    ).sum()
    if dur_min < 0.1:
        continue

    ho_total = adf["handover_detected"].astype(str).str.lower().eq("true").sum() \
               if "handover_detected" in adf.columns else 0
    pp_total = adf["ping_pong_detected"].astype(str).str.lower().eq("true").sum() \
               if "ping_pong_detected" in adf.columns else 0

    ho_stats.append({
        "act": ACT_KO[act], "color": ACT_COLOR[act],
        "ho_per_min": ho_total / dur_min,
        "pp_per_min": pp_total / dur_min,
        "normal_per_min": (ho_total - pp_total) / dur_min,
        "ho_total": ho_total, "pp_total": pp_total, "dur_min": round(dur_min, 1),
    })

ho_df = pd.DataFrame(ho_stats).sort_values("ho_per_min", ascending=True)

fig, axes = plt.subplots(1, 2, figsize=(13, 5))

# 왼쪽: 활동별 핸드오버 빈도 (스택 바)
x = range(len(ho_df))
axes[0].barh(x, ho_df["normal_per_min"], color=[ACT_COLOR.get(a.split("(")[1].rstrip(")"), "#888") for a in ho_df["act"]], alpha=0.8, label="일반 HO")
axes[0].barh(x, ho_df["pp_per_min"],
             left=ho_df["normal_per_min"], color="red", alpha=0.7, label="핑퐁 HO")
axes[0].set_yticks(x)
axes[0].set_yticklabels(ho_df["act"], fontsize=10)
axes[0].set_xlabel("핸드오버 빈도 (회/분)", fontsize=10)
axes[0].set_title("활동별 핸드오버 빈도", fontsize=12, fontweight="bold")
axes[0].legend(fontsize=9)
for i, row in ho_df.reset_index(drop=True).iterrows():
    axes[0].text(row["ho_per_min"] + 0.02, i,
                 f"{row['ho_per_min']:.2f}/분 ({row['dur_min']}분)",
                 va="center", fontsize=8)

# 오른쪽: 핑퐁 비율 파이 (subway만)
sub_ho = ho_df[ho_df["act"].str.contains("subway")]
if not sub_ho.empty:
    row = sub_ho.iloc[0]
    normal = row["ho_total"] - row["pp_total"]
    pp     = row["pp_total"]
    if row["ho_total"] > 0:
        wedges, texts, autotexts = axes[1].pie(
            [normal, pp],
            labels=["일반 핸드오버", "핑퐁 핸드오버"],
            colors=["#4C72B0", "#C44E52"],
            autopct="%1.1f%%", startangle=90,
            wedgeprops=dict(edgecolor="white", linewidth=2),
            textprops=dict(fontsize=11),
        )
        for at in autotexts:
            at.set_fontsize(12)
            at.set_fontweight("bold")
        axes[1].set_title(
            f"지하철 핸드오버 유형 분포\n(총 {int(row['ho_total'])}건)",
            fontsize=12, fontweight="bold"
        )
else:
    axes[1].text(0.5, 0.5, "지하철 데이터 없음", ha="center", va="center", transform=axes[1].transAxes)

fig.suptitle("핸드오버 패턴 분석", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(OUT_DIR / "viz_03_handover_analysis.png")
plt.close()
print("  → viz_03 저장")

# ══════════════════════════════════════════════════════════════════
# VIZ 04 — 신호-처리량 무상관 (이슈 3 시각화)
# ══════════════════════════════════════════════════════════════════
print("[4/6] 신호-처리량 무상관 시각화...")

fig, axes = plt.subplots(1, 3, figsize=(15, 5))
pairs = [
    ("rsrp_dbm",   "RSRP (dBm)"),
    ("sinr_snr_db","SINR (dB)"),
    ("rsrq_db",    "RSRQ (dB)"),
]

for ax, (xcol, xlabel) in zip(axes, pairs):
    if xcol not in new_df.columns:
        continue
    sub = new_df[[xcol, rx_col, "_activity"]].copy()
    sub[xcol]   = pd.to_numeric(sub[xcol],   errors="coerce")
    sub[rx_col] = pd.to_numeric(sub[rx_col], errors="coerce")
    sub = sub.dropna()
    sub = sub[sub[rx_col] > 0]  # 트래픽 없는 구간 제외

    colors = [ACT_COLOR.get(a, "#888") for a in sub["_activity"]]

    # Hexbin
    hb = ax.hexbin(sub[xcol], sub[rx_col],
                   gridsize=30, cmap="YlOrRd", mincnt=1,
                   xscale="linear", yscale="linear", linewidths=0.3)

    # 추세선
    z  = np.polyfit(sub[xcol], sub[rx_col], 1)
    xr = np.linspace(sub[xcol].min(), sub[xcol].max(), 200)
    ax.plot(xr, np.poly1d(z)(xr), "k--", linewidth=1.5, label="추세선")

    r = sub[[xcol, rx_col]].corr().iloc[0, 1]
    ax.set_xlabel(xlabel, fontsize=11)
    ax.set_ylabel("셀룰러 Rx (Mbps)" if ax == axes[0] else "", fontsize=11)
    ax.set_title(f"{xlabel} vs Rx\nr = {r:.3f}", fontsize=11, fontweight="bold")
    ax.legend(fontsize=9)

    # 컬러바
    cb = fig.colorbar(hb, ax=ax, pad=0.02)
    cb.set_label("샘플 수", fontsize=8)

fig.suptitle(
    "신호 품질 지표 vs 셀룰러 처리량 (TrafficStats 수동 측정 한계 검증)\n"
    "※ r ≈ 0 → 신호 세기와 실측 처리량은 사실상 무상관 (수요 기반 측정의 한계)",
    fontsize=11, fontweight="bold"
)
plt.tight_layout()
plt.savefig(OUT_DIR / "viz_04_signal_vs_throughput.png")
plt.close()
print("  → viz_04 저장")

# ══════════════════════════════════════════════════════════════════
# VIZ 05 — SINR 결측 패턴 (이동 환경 vs 고정 환경)
# ══════════════════════════════════════════════════════════════════
print("[5/6] SINR 결측 패턴...")

fig, axes = plt.subplots(1, 2, figsize=(13, 5))

# 왼쪽: 파일별 결측률 바차트
sinr_miss = []
for fname in sorted(new_df["_file"].unique()):
    fdf  = new_df[new_df["_file"] == fname]
    act  = fdf["_activity"].iloc[0]
    miss = pd.to_numeric(fdf["sinr_snr_db"], errors="coerce").isna().mean() * 100
    label = fname[12:21] + f"\n({ACT_KO.get(act, act)})"
    sinr_miss.append({"label": label, "miss": miss, "act": act})

sm_df = pd.DataFrame(sinr_miss)
bars = axes[0].bar(
    range(len(sm_df)), sm_df["miss"],
    color=[ACT_COLOR.get(a, "#888") for a in sm_df["act"]],
    edgecolor="white", linewidth=0.5, alpha=0.85
)
axes[0].set_xticks(range(len(sm_df)))
axes[0].set_xticklabels(sm_df["label"], fontsize=7.5, rotation=30, ha="right")
axes[0].set_ylabel("SINR 결측률 (%)", fontsize=10)
axes[0].set_ylim(0, 100)
axes[0].set_title("세션별 SINR 결측률", fontsize=12, fontweight="bold")
for bar, row in zip(bars, sm_df.itertuples()):
    axes[0].text(bar.get_x() + bar.get_width() / 2,
                 bar.get_height() + 1, f"{row.miss:.0f}%",
                 ha="center", fontsize=7.5)

legend_patches = [mpatches.Patch(color=c, label=ACT_KO[a]) for a, c in ACT_COLOR.items() if a in sm_df["act"].values]
axes[0].legend(handles=legend_patches, fontsize=8, loc="upper right")

# 오른쪽: 유효 SINR 분포 (활동별 KDE)
axes[1].axvline(0,  color="red",   linestyle="--", linewidth=1, alpha=0.7, label="0 dB")
axes[1].axvline(10, color="green", linestyle="--", linewidth=1, alpha=0.7, label="10 dB (양호)")
for act in acts:
    vals = pd.to_numeric(
        new_df[new_df["_activity"] == act]["sinr_snr_db"], errors="coerce"
    ).dropna()
    if len(vals) < 10:
        continue
    vals.plot.kde(ax=axes[1], color=ACT_COLOR[act], linewidth=2, label=ACT_KO[act])

axes[1].set_xlabel("SINR (dB)", fontsize=10)
axes[1].set_ylabel("밀도", fontsize=10)
axes[1].set_title("활동별 SINR 분포 (유효값)", fontsize=12, fontweight="bold")
axes[1].legend(fontsize=9)
axes[1].set_xlim(-15, 35)

fig.suptitle("SINR 결측 현황 및 분포 분석", fontsize=13, fontweight="bold")
plt.tight_layout()
plt.savefig(OUT_DIR / "viz_05_sinr_analysis.png")
plt.close()
print("  → viz_05 저장")

# ══════════════════════════════════════════════════════════════════
# VIZ 06 — 상관관계 히트맵 (셀룰러 Rx 기준)
# ══════════════════════════════════════════════════════════════════
print("[6/6] 상관관계 히트맵...")

cols_for_corr = {
    "rsrp_dbm":    "RSRP (dBm)",
    "rsrq_db":     "RSRQ (dB)",
    "sinr_snr_db": "SINR (dB)",
    "signal_level":"신호레벨 (0-4)",
    "neighbor_count":   "이웃셀 수",
    "timing_advance_lte": "TA (LTE)",
    "ho_count_30s": "HO 빈도 (30s)",
    rx_col:        "셀룰러 Rx (Mbps)",
    "gps_speed_ms": "이동속도 (m/s)",
}
avail = {k: v for k, v in cols_for_corr.items() if k in new_df.columns}

corr_df = new_df[list(avail.keys())].apply(pd.to_numeric, errors="coerce")
corr_df = corr_df.rename(columns=avail)
corr_matrix = corr_df.corr()

fig, ax = plt.subplots(figsize=(10, 8))
mask = np.triu(np.ones_like(corr_matrix, dtype=bool))

hm = sns.heatmap(
    corr_matrix, mask=mask,
    annot=True, fmt=".2f", annot_kws={"size": 10, "fontweight": "bold"},
    cmap="RdYlGn", center=0, vmin=-1, vmax=1,
    square=True, linewidths=0.5, linecolor="white",
    cbar_kws={"shrink": 0.8, "label": "피어슨 r"},
    ax=ax
)

ax.set_title(
    "신호 품질 지표 간 상관관계 히트맵\n"
    "※ 셀룰러 처리량(mobile_rx) 기준",
    fontsize=12, fontweight="bold"
)
ax.tick_params(axis="x", rotation=30)
ax.tick_params(axis="y", rotation=0)
plt.tight_layout()
plt.savefig(OUT_DIR / "viz_06_correlation_heatmap.png")
plt.close()
print("  → viz_06 저장")

# ── 완료 ──────────────────────────────────────────────────────────
print("\n" + "="*60)
print("생성 완료!")
print(f"저장 위치: {OUT_DIR}\n")
for p in sorted(OUT_DIR.glob("viz_*.png")):
    descs = {
        "viz_01": "활동별 RSRP·SINR 비교 (바이올린+박스)",
        "viz_02": "지하철 RSRP 시계열 + 핸드오버 마커",
        "viz_03": "핸드오버 빈도 + 핑퐁 비율",
        "viz_04": "신호 vs 처리량 무상관 (hexbin)",
        "viz_05": "SINR 결측 패턴 + 분포 비교",
        "viz_06": "지표 간 상관관계 히트맵",
    }
    print(f"  {p.name}  —  {descs.get(p.stem, '')}")
