"""
NetworkTrackerApp data analysis
Feedback: data description / NCI patterns / datarate viz / correlation / README verification
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import os
import json
import warnings
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from pathlib import Path

warnings.filterwarnings("ignore")

# ── 경로 설정 ──────────────────────────────────────────────────
CSV_DIR   = Path(r"C:\Users\admin\NetworkTrackerApp\trackingcsv")
OUT_DIR   = Path(r"C:\Users\admin\NetworkTrackerApp\analysis_output")
OUT_DIR.mkdir(exist_ok=True)

FONT = "Malgun Gothic"     # Windows 한글 폰트
plt.rcParams["font.family"]       = FONT
plt.rcParams["axes.unicode_minus"] = False
sns.set_theme(style="whitegrid", font=FONT)

# ── 파일별 활동 정보 (파일명 → 설명) ───────────────────────────
ACTIVITY_META = {
    "network_log_20260506_075510_unknown.csv": ("2026-05-06 07:55", "불명 (unknown) - 수집 위치: 서울 성동구 (37.550N, 127.074E)"),
    "network_log_20260506_163853_unknown.csv": ("2026-05-06 16:38", "불명 (unknown) - 오후 수집"),
    "network_log_20260512_123412_walking.csv": ("2026-05-12 12:34", "도보 이동 (walking) - 성동구 일대"),
    "network_log_20260512_125212_subway.csv":  ("2026-05-12 12:52", "지하철 이동 (subway) - 성동구 → 이동"),
    "network_log_20260512_160738_subway.csv":  ("2026-05-12 16:07", "지하철 이동 (subway)"),
    "network_log_20260517_160607_subway.csv":  ("2026-05-17 16:06", "지하철 이동 (subway)"),
    "network_log_20260524_123419_car.csv":     ("2026-05-24 12:34", "차량 이동 (car)"),
    "network_log_20260524_163715_subway.csv":  ("2026-05-24 16:37", "지하철 이동 (subway)"),
    "network_log_20260526_125145_subway.csv":  ("2026-05-26 12:51", "지하철 이동 (subway)"),
    "network_log_20260527_131649_walking.csv": ("2026-05-27 13:16", "도보 이동 (walking)"),
    "network_log_20260529_135952_home.csv":    ("2026-05-29 13:59", "실내 고정 (home) - 집"),
}

# ── 1. 데이터 로드 ─────────────────────────────────────────────
def load_all():
    frames = []
    for f in sorted(CSV_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(f, low_memory=False)
            df["_file"] = f.name
            # 구버전 컬럼명 통일
            if "sinr_db" in df.columns and "sinr_snr_db" not in df.columns:
                df = df.rename(columns={"sinr_db": "sinr_snr_db",
                                        "rx_speed_bps": "rx_speed_Bps",
                                        "tx_speed_bps": "tx_speed_Bps"})
            # activity 태그
            tag = f.name.rsplit("_", 1)[-1].replace(".csv", "")
            df["_activity"] = tag
            df["_datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            frames.append(df)
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
    return pd.concat(frames, ignore_index=True)

all_df = load_all()
print(f"전체 레코드: {len(all_df):,}행  |  파일 수: {all_df['_file'].nunique()}")

# ── 2. 데이터 설명 출력 ─────────────────────────────────────────
print("\n" + "="*70)
print("【1】 데이터 설명 - 언제 / 어디서 / 무엇을 하며 수집했는가")
print("="*70)
summary_rows = []
for f, (dt_str, desc) in ACTIVITY_META.items():
    fdf = all_df[all_df["_file"] == f]
    if fdf.empty:
        continue
    n       = len(fdf)
    dur_s   = (fdf["_datetime"].max() - fdf["_datetime"].min()).total_seconds()
    lat_c   = fdf["latitude"].mean() if "latitude" in fdf.columns else float("nan")
    lon_c   = fdf["longitude"].mean() if "longitude" in fdf.columns else float("nan")
    is5g    = fdf["is_5g"].astype(str).str.lower().eq("true").mean() * 100 if "is_5g" in fdf.columns else 0
    row = dict(파일=f, 시작=dt_str, 활동=desc, 행수=n,
               수집시간_분=round(dur_s/60,1), 위도=round(lat_c,4), 경도=round(lon_c,4),
               _5G비율pct=round(is5g,1))
    summary_rows.append(row)
    print(f"\n  파일: {f}")
    print(f"  시작: {dt_str}  |  {desc}")
    print(f"  행수: {n}행  |  수집시간: {round(dur_s/60,1)}분  |  5G표시비율: {round(is5g,1)}%")
    if not np.isnan(lat_c):
        print(f"  위치: ({lat_c:.4f}, {lon_c:.4f})")

# ── 3. NCI 패턴 분석 ───────────────────────────────────────────
print("\n" + "="*70)
print("【2】 NCI (NR Cell Identity) 패턴 분석")
print("="*70)

# NCI는 serving_cell_id에서 NR 서빙 셀인 경우, 또는 neighbors_json 의 NR 이웃 셀에서 추출
nci_values = []
lte_ci_values = []

for _, row in all_df.iterrows():
    # NR serving cell ID (is_5g_actual=True 이거나 network_type=NR(5G) 인 경우)
    net_type = str(row.get("network_type", "")).upper()
    is_5g_actual = str(row.get("is_5g_actual", "false")).lower() == "true"
    nr_serving = str(row.get("nr_serving_cell_seen", "false")).lower() == "true"

    cell_id = str(row.get("serving_cell_id", "")).strip()
    if cell_id and cell_id not in ("", "nan"):
        if "NR" in net_type or is_5g_actual or nr_serving:
            try:
                nci_values.append(int(cell_id))
            except:
                pass
        elif "LTE" in net_type:
            try:
                lte_ci_values.append(int(cell_id))
            except:
                pass

    # neighbors_json 에서 NR 셀 NCI 추출
    nbr = row.get("neighbors_json", "")
    if isinstance(nbr, str) and "NR" in nbr:
        try:
            cells = json.loads(nbr)
            for c in cells:
                if "NR" in str(c.get("type", "")):
                    nci = c.get("nci") or c.get("cell_id")
                    if nci and str(nci) != "2147483647":
                        nci_values.append(int(nci))
        except:
            pass

print(f"\n  서빙/이웃 NCI 수집 건수: {len(nci_values)}")
print(f"  LTE CI 수집 건수: {len(lte_ci_values)}")

if nci_values:
    nci_series = pd.Series(nci_values)
    print(f"\n  NCI 유니크 값 수: {nci_series.nunique()}")
    print(f"  NCI 범위: {nci_series.min():,} ~ {nci_series.max():,}")
    print(f"  상위 NCI 빈도:")
    for val, cnt in nci_series.value_counts().head(10).items():
        print(f"    NCI={val:>15,}  → {cnt}회")
    # NCI bit 분석 (36-bit: gNB-ID 22bit + sector 14bit)
    print(f"\n  NCI 비트 분석 (36-bit: gNB-ID=상위22bit, 셀ID=하위14bit):")
    for val in nci_series.unique()[:5]:
        gnb  = val >> 14
        cell = val & 0x3FFF
        print(f"    NCI={val}  →  gNB-ID={gnb}, 셀ID={cell}")
else:
    print("\n  ⚠ 데이터 내 유효 NCI 없음 — 모든 수집이 LTE(4G) 서빙 셀 기반")
    print("  (5G NSA 환경에서는 LTE가 serving cell이므로 NCI=미제공)")
    # is_5g_display=True 레코드 비율
    if "is_5g_display" in all_df.columns:
        nsa_rows = all_df["is_5g_display"].astype(str).str.lower().eq("true").sum()
        print(f"  5G NSA 표시 레코드: {nsa_rows}행 ({nsa_rows/len(all_df)*100:.1f}%) — NR 보조 데이터만, serving cell은 LTE")

if lte_ci_values:
    lci = pd.Series(lte_ci_values)
    print(f"\n  LTE CI 유니크 기지국 수: {lci.nunique()}")
    print(f"  상위 LTE CI (빈도):")
    for val, cnt in lci.value_counts().head(8).items():
        print(f"    CI={val}  → {cnt}회")

# ── 4. 데이터레이트 시각화 ─────────────────────────────────────
print("\n" + "="*70)
print("【3】 데이터레이트 (Rx/Tx) 시각화")
print("="*70)

rx_col = "rx_bitrate_Mbps" if "rx_bitrate_Mbps" in all_df.columns else "rx_speed_Bps"
tx_col = "tx_bitrate_Mbps" if "tx_bitrate_Mbps" in all_df.columns else "tx_speed_Bps"

files = sorted(all_df["_file"].unique())
n_files = len(files)
fig, axes = plt.subplots(n_files, 1, figsize=(14, 3 * n_files))
if n_files == 1:
    axes = [axes]
fig.suptitle("세션별 Rx/Tx 데이터레이트 (Mbps)", fontsize=14, fontweight="bold")

for ax, fname in zip(axes, files):
    fdf = all_df[all_df["_file"] == fname].copy()
    fdf = fdf.sort_values("_datetime")
    t = fdf["_datetime"]

    rx = pd.to_numeric(fdf[rx_col], errors="coerce")
    tx = pd.to_numeric(fdf[tx_col], errors="coerce")
    if rx_col.endswith("Bps"):
        rx = rx * 8 / 1e6
        tx = tx * 8 / 1e6

    ax.fill_between(t, rx, alpha=0.6, color="royalblue", label="Rx (Mbps)")
    ax.fill_between(t, -tx, alpha=0.6, color="tomato", label="Tx (Mbps)")
    ax.axhline(0, color="black", linewidth=0.5)
    tag = fname.rsplit("_", 1)[-1].replace(".csv", "")
    ax.set_title(f"{fname}  [{tag}]", fontsize=9)
    ax.set_ylabel("Mbps")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.legend(loc="upper right", fontsize=8)

plt.tight_layout()
out_path = OUT_DIR / "01_datarate_per_session.png"
plt.savefig(out_path, dpi=150)
plt.close()
print(f"  저장: {out_path}")

# ── 데이터레이트 통계 ───────────────────────────────────────────
print("\n  세션별 Rx 데이터레이트 통계 (Mbps):")
for fname in files:
    fdf = all_df[all_df["_file"] == fname]
    rx  = pd.to_numeric(fdf[rx_col], errors="coerce")
    if rx_col.endswith("Bps"):
        rx = rx * 8 / 1e6
    tag = fname.rsplit("_", 1)[-1].replace(".csv", "")
    print(f"  [{tag:>8}] {fname[-35:]}  "
          f"mean={rx.mean():.2f}  max={rx.max():.2f}  "
          f"p95={rx.quantile(0.95):.2f} Mbps")

# ── 5. 전체 요약 박스플롯: 활동별 Rx ───────────────────────────
fig, ax = plt.subplots(figsize=(10, 5))
plot_df = all_df.copy()
rx = pd.to_numeric(plot_df[rx_col], errors="coerce")
if rx_col.endswith("Bps"):
    rx = rx * 8 / 1e6
plot_df["rx_Mbps"] = rx
plot_df = plot_df[plot_df["rx_Mbps"] > 0]  # 0 제외

order = plot_df.groupby("_activity")["rx_Mbps"].median().sort_values().index.tolist()
sns.boxplot(data=plot_df, x="_activity", y="rx_Mbps", order=order,
            palette="Set2", ax=ax, showfliers=False)
ax.set_title("활동 유형별 Rx 데이터레이트 분포 (0 제외)", fontweight="bold")
ax.set_xlabel("활동 유형")
ax.set_ylabel("Rx (Mbps)")
plt.tight_layout()
out_path = OUT_DIR / "02_datarate_by_activity.png"
plt.savefig(out_path, dpi=150)
plt.close()
print(f"\n  저장: {out_path}")

# ── 6. 상관관계 분석 ────────────────────────────────────────────
print("\n" + "="*70)
print("【4】 상관관계 분석")
print("="*70)

# 최신 형식 컬럼만 있는 레코드로 한정
num_cols = ["rsrp_dbm", "rsrq_db", "rssi_dbm", "sinr_snr_db", "signal_level",
            "neighbor_count", "timing_advance_lte",
            rx_col, tx_col, "gps_accuracy_m"]
if "gps_speed_ms" in all_df.columns:
    num_cols.append("gps_speed_ms")

avail = [c for c in num_cols if c in all_df.columns]
corr_df = all_df[avail].apply(pd.to_numeric, errors="coerce").dropna(how="all")

# rx_speed_Bps → Mbps 변환
if rx_col.endswith("Bps"):
    corr_df[rx_col] = corr_df[rx_col] * 8 / 1e6
    corr_df[tx_col] = corr_df[tx_col] * 8 / 1e6
    corr_df = corr_df.rename(columns={rx_col: "rx_Mbps", tx_col: "tx_Mbps"})
else:
    corr_df = corr_df.rename(columns={rx_col: "rx_Mbps", tx_col: "tx_Mbps"})

rename_map = {
    "rsrp_dbm": "RSRP(dBm)", "rsrq_db": "RSRQ(dB)",
    "rssi_dbm": "RSSI(dBm)", "sinr_snr_db": "SINR(dB)",
    "signal_level": "신호레벨(0-4)", "neighbor_count": "이웃셀수",
    "timing_advance_lte": "TA(거리)", "gps_accuracy_m": "GPS정확도(m)",
    "gps_speed_ms": "이동속도(m/s)", "rx_Mbps": "Rx(Mbps)", "tx_Mbps": "Tx(Mbps)"
}
corr_df = corr_df.rename(columns=rename_map)
corr_df = corr_df[corr_df["Rx(Mbps)"].notna()]

corr_matrix = corr_df.corr(numeric_only=True)

fig, ax = plt.subplots(figsize=(11, 9))
mask = np.triu(np.ones_like(corr_matrix, dtype=bool))
sns.heatmap(corr_matrix, mask=mask, annot=True, fmt=".2f", cmap="RdYlGn",
            center=0, vmin=-1, vmax=1, square=True, ax=ax,
            annot_kws={"size": 9})
ax.set_title("신호 지표 × 데이터레이트 상관관계 히트맵", fontsize=13, fontweight="bold")
plt.tight_layout()
out_path = OUT_DIR / "03_correlation_heatmap.png"
plt.savefig(out_path, dpi=150)
plt.close()
print(f"\n  저장: {out_path}")

# 주요 상관계수 출력
rx_corr = corr_matrix["Rx(Mbps)"].drop("Rx(Mbps)").sort_values(key=abs, ascending=False)
print("\n  Rx(Mbps) 와의 상관계수 (절댓값 큰 순):")
for col, val in rx_corr.items():
    direction = "↑ 높을수록 Rx 증가" if val > 0 else "↓ 낮을수록 Rx 증가"
    print(f"  {col:>15s}  r={val:+.3f}  {direction}")

# ── 7. 산점도: RSRP vs Rx ───────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(15, 5))
pairs = [("RSRP(dBm)", "Rx(Mbps)"), ("SINR(dB)", "Rx(Mbps)"), ("RSRQ(dB)", "Rx(Mbps)")]
for ax, (xc, yc) in zip(axes, pairs):
    sub = corr_df[[xc, yc, "신호레벨(0-4)"]].dropna()
    sub = sub[sub[yc] > 0]
    sc = ax.scatter(sub[xc], sub[yc], c=sub["신호레벨(0-4)"],
                    cmap="RdYlGn", alpha=0.5, s=18, vmin=0, vmax=4)
    # 선형 추세선
    z = np.polyfit(sub[xc].dropna(), sub[yc].dropna(), 1)
    xr = np.linspace(sub[xc].min(), sub[xc].max(), 100)
    ax.plot(xr, np.poly1d(z)(xr), "k--", linewidth=1.2)
    r = sub[[xc, yc]].corr().iloc[0, 1]
    ax.set_xlabel(xc)
    ax.set_ylabel(yc)
    ax.set_title(f"{xc} vs {yc}  (r={r:.2f})")
plt.colorbar(sc, ax=axes[-1], label="신호레벨(0-4)")
plt.suptitle("신호 품질 지표 vs Rx 데이터레이트 산점도", fontsize=12, fontweight="bold")
plt.tight_layout()
out_path = OUT_DIR / "04_scatter_signal_vs_rx.png"
plt.savefig(out_path, dpi=150)
plt.close()
print(f"\n  저장: {out_path}")

# ── 8. 신호 강도 시계열 (세션별) ───────────────────────────────
fig, axes = plt.subplots(n_files, 1, figsize=(14, 3 * n_files))
if n_files == 1:
    axes = [axes]
fig.suptitle("세션별 RSRP (신호 세기) 시계열", fontsize=13, fontweight="bold")

for ax, fname in zip(axes, files):
    fdf = all_df[all_df["_file"] == fname].sort_values("_datetime")
    rsrp = pd.to_numeric(fdf["rsrp_dbm"], errors="coerce")
    t    = fdf["_datetime"]
    ax.plot(t, rsrp, linewidth=1, color="steelblue")
    ax.axhline(-80, color="green", linestyle="--", linewidth=0.8, label="-80 (양호)")
    ax.axhline(-100, color="red", linestyle="--", linewidth=0.8, label="-100 (불량)")
    ax.set_ylim(-120, -40)
    tag = fname.rsplit("_", 1)[-1].replace(".csv", "")
    ax.set_title(f"{fname}  [{tag}]", fontsize=9)
    ax.set_ylabel("RSRP (dBm)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.legend(loc="upper right", fontsize=7)

plt.tight_layout()
out_path = OUT_DIR / "05_rsrp_timeseries.png"
plt.savefig(out_path, dpi=150)
plt.close()
print(f"\n  저장: {out_path}")

# ── 9. 활동별 신호 박스플롯 ────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(15, 5))
metrics = [("rsrp_dbm", "RSRP (dBm)"), ("sinr_snr_db", "SINR (dB)"), ("signal_level", "신호레벨 (0-4)")]
for ax, (col, label) in zip(axes, metrics):
    if col not in all_df.columns:
        continue
    tmp = all_df[["_activity", col]].copy()
    tmp[col] = pd.to_numeric(tmp[col], errors="coerce")
    order = tmp.groupby("_activity")[col].median().sort_values().index.tolist()
    sns.boxplot(data=tmp, x="_activity", y=col, order=order, palette="Set3", ax=ax, showfliers=False)
    ax.set_title(f"활동별 {label}", fontweight="bold")
    ax.set_xlabel("활동 유형")
    ax.set_ylabel(label)
    ax.tick_params(axis="x", rotation=15)
plt.suptitle("활동 유형별 신호 품질 비교", fontsize=13, fontweight="bold")
plt.tight_layout()
out_path = OUT_DIR / "06_signal_by_activity.png"
plt.savefig(out_path, dpi=150)
plt.close()
print(f"\n  저장: {out_path}")

# ── 10. GPT(README) 내용 검증 ─────────────────────────────────
print("\n" + "="*70)
print("【5】 README(GPT) 기술 내용 검증")
print("="*70)

checks = []

# ① 5G NSA 판단 로직 검증: is_5g_display=True + is_5g_actual=False → NSA
if "is_5g_display" in all_df.columns and "is_5g_actual" in all_df.columns:
    nsa_mask = (all_df["is_5g_display"].astype(str).str.lower().eq("true") &
                all_df["is_5g_actual"].astype(str).str.lower().eq("false"))
    nsa_serving_lte = all_df[nsa_mask]["network_type"].astype(str).str.upper().str.contains("LTE").mean()
    check = "✅ PASS" if nsa_serving_lte > 0.9 else "❌ FAIL"
    checks.append((check, "5G NSA(display=T, actual=F) 환경에서 serving cell=LTE",
                   f"LTE serving 비율: {nsa_serving_lte*100:.1f}%"))

# ② RSRP 범위 (-140~-44 dBm)
rsrp = pd.to_numeric(all_df["rsrp_dbm"], errors="coerce").dropna()
rsrp_in_range = ((rsrp >= -140) & (rsrp <= -44)).mean()
check = "✅ PASS" if rsrp_in_range > 0.99 else f"⚠ PARTIAL ({rsrp_in_range*100:.1f}%)"
checks.append((check, "RSRP 범위 -140~-44 dBm", f"실제 범위: {rsrp.min():.0f}~{rsrp.max():.0f} dBm, 적합율: {rsrp_in_range*100:.1f}%"))

# ③ RSRP -80 이상이면 양호
good_rsrp = (rsrp >= -80).mean()
avg_rsrp  = rsrp.mean()
check = "✅ PASS" if avg_rsrp > -90 else "⚠ 신호 약함"
checks.append((check, "RSRP -80 이상이면 양호", f"평균 RSRP: {avg_rsrp:.1f} dBm, -80 이상 비율: {good_rsrp*100:.1f}%"))

# ④ RSRQ 범위 (-20~-3 dB)
if "rsrq_db" in all_df.columns:
    rsrq = pd.to_numeric(all_df["rsrq_db"], errors="coerce").dropna()
    rsrq_in_range = ((rsrq >= -20) & (rsrq <= -3)).mean()
    check = "✅ PASS" if rsrq_in_range > 0.8 else f"⚠ PARTIAL ({rsrq_in_range*100:.1f}%)"
    checks.append((check, "RSRQ 범위 -20~-3 dB (일반적)", f"실제 범위: {rsrq.min():.0f}~{rsrq.max():.0f} dB, 적합율: {rsrq_in_range*100:.1f}%"))

# ⑤ signal_level 0~4 범위
sl = pd.to_numeric(all_df["signal_level"], errors="coerce").dropna()
sl_ok = ((sl >= 0) & (sl <= 4)).all()
check = "✅ PASS" if sl_ok else "❌ FAIL"
checks.append((check, "signal_level 범위 0~4", f"실제 범위: {int(sl.min())}~{int(sl.max())}"))

# ⑥ TrafficStats 기반 처리량 — Wi-Fi/셀룰러 구분 없음 (README 경고)
# → rx값이 매우 클 수 있는지 확인
rx_mbps = pd.to_numeric(all_df[rx_col], errors="coerce")
if rx_col.endswith("Bps"):
    rx_mbps = rx_mbps * 8 / 1e6
high_rx = (rx_mbps > 100).mean()  # 100 Mbps 이상은 보통 Wi-Fi 포함
checks.append(("ℹ INFO", "TrafficStats = Wi-Fi+셀룰러 합산 (README 경고 항목)",
               f"100Mbps 초과 레코드 {high_rx*100:.1f}% — Wi-Fi 포함 가능성 검토 필요"))

# ⑦ LTE Timing Advance 범위 0~1282
if "timing_advance_lte" in all_df.columns:
    ta = pd.to_numeric(all_df["timing_advance_lte"], errors="coerce").dropna()
    ta_ok = ((ta >= 0) & (ta <= 1282)).mean()
    check = "✅ PASS" if ta_ok > 0.99 else f"⚠ {ta_ok*100:.1f}% 범위 내"
    checks.append((check, "LTE TA 범위 0~1282", f"실제 범위: {ta.min():.0f}~{ta.max():.0f}"))

# ⑧ 5초 수집 주기 검증
dt_sorted = all_df.sort_values(["_file", "_datetime"])
dt_sorted["_delta_s"] = dt_sorted.groupby("_file")["_datetime"].diff().dt.total_seconds()
intervals  = dt_sorted["_delta_s"].dropna()
near5      = ((intervals >= 4) & (intervals <= 7)).mean()
med_int    = intervals.median()
check = "✅ PASS" if near5 > 0.85 else f"⚠ PARTIAL ({near5*100:.1f}%)"
checks.append((check, "수집 주기 5초 (±2초)", f"중앙값: {med_int:.1f}초, 4-7초 구간 비율: {near5*100:.1f}%"))

print()
for status, claim, evidence in checks:
    print(f"  {status}  {claim}")
    print(f"           → {evidence}\n")

# ── 완료 메시지 ────────────────────────────────────────────────
print("="*70)
print(f"분석 완료! 시각화 파일 저장 위치:\n  {OUT_DIR}")
print("="*70)
print("  01_datarate_per_session.png  — 세션별 Rx/Tx 데이터레이트")
print("  02_datarate_by_activity.png  — 활동별 Rx 분포 박스플롯")
print("  03_correlation_heatmap.png   — 전체 지표 상관관계 히트맵")
print("  04_scatter_signal_vs_rx.png  — 신호 지표 vs Rx 산점도")
print("  05_rsrp_timeseries.png       — 세션별 RSRP 시계열")
print("  06_signal_by_activity.png    — 활동별 신호 품질 비교")
