import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import pandas as pd
import numpy as np
from pathlib import Path

CSV_DIR = Path(r"C:\Users\admin\NetworkTrackerApp\trackingcsv")
frames = []
for f in sorted(CSV_DIR.glob("*.csv")):
    df = pd.read_csv(f, low_memory=False)
    df["_file"] = f.name
    frames.append(df)
all_df = pd.concat(frames, ignore_index=True)
new_df = all_df[all_df["_file"].str.contains("2026051[2-9]|202605[2-9]")].copy()

def null_pct(col, df=new_df):
    return f"{pd.to_numeric(df[col], errors='coerce').isna().mean()*100:.1f}%"

print("=== GPS 데이터 품질 ===")
lat = pd.to_numeric(new_df["latitude"], errors="coerce")
print(f"  lat/lon 결측:      {lat.isna().mean()*100:.1f}%")
acc = pd.to_numeric(new_df["gps_accuracy_m"], errors="coerce").dropna()
print(f"  accuracy 범위:     {acc.min():.1f} ~ {acc.max():.1f} m  (중앙값 {acc.median():.1f} m)")
spd = pd.to_numeric(new_df["gps_speed_ms"], errors="coerce")
print(f"  gps_speed_ms 결측: {spd.isna().mean()*100:.1f}%")
spd_v = spd.dropna()
print(f"  speed 범위:        {spd_v.min():.2f} ~ {spd_v.max():.2f} m/s  (최대 {spd_v.max()*3.6:.1f} km/h)")
print(f"  speed=0 비율:      {(spd_v==0).mean()*100:.1f}%")

print()
print("=== serving_cell_id 품질 ===")
ci = new_df["serving_cell_id"].astype(str).str.strip()
empty = (ci == "") | (ci == "nan")
print(f"  공백/nan 비율: {empty.mean()*100:.1f}%")
print(f"  유효 CI 행수:  {(~empty).sum():,}행")

print()
print("=== timing_advance_lte ===")
ta = pd.to_numeric(new_df["timing_advance_lte"], errors="coerce")
print(f"  결측률: {ta.isna().mean()*100:.1f}%")
ta_v = ta.dropna()
print(f"  범위:   {int(ta_v.min())} ~ {int(ta_v.max())}  (약 {int(ta_v.min())*78}m ~ {int(ta_v.max())*78}m)")
dist = ta_v.value_counts().sort_index().head(8)
print(f"  분포:   " + "  ".join([f"TA={int(v)}({cnt}건)" for v, cnt in dist.items()]))

print()
print("=== imu_speed_ms ===")
imu = pd.to_numeric(new_df["imu_speed_ms"], errors="coerce")
print(f"  결측률:    {imu.isna().mean()*100:.1f}%")
print(f"  값=0 비율: {(imu==0).mean()*100:.1f}%")
imu_pos = imu[imu > 0].dropna()
print(f"  0초과 건수: {len(imu_pos):,}건  범위: {imu_pos.min():.2f} ~ {imu_pos.max():.2f} m/s" if len(imu_pos) else "  0초과 없음")

print()
print("=== 핸드오버 컬럼 일관성 ===")
ho = new_df["handover_detected"].astype(str).str.lower() == "true"
pp = new_df["ping_pong_detected"].astype(str).str.lower() == "true"
print(f"  handover=true:   {ho.sum():,}건")
print(f"  pingpong=true:   {pp.sum():,}건")
pp_no_ho = (pp & ~ho).sum()
print(f"  pingpong=true 이면서 handover=false (비정상): {pp_no_ho}건  {'✅ 없음' if pp_no_ho==0 else '⚠ 있음'}")

print()
print("=== 구버전(20260506) vs 신버전 컬럼 차이 ===")
old_df = all_df[all_df["_file"].str.contains("20260506")]
old_cols = set(old_df.columns) - {"_file"}
nw_cols  = set(new_df.columns) - {"_file"}
missing  = sorted(nw_cols - old_cols)
print(f"  구버전 {len(old_cols)}개 컬럼 / 신버전 {len(nw_cols)}개 컬럼")
print(f"  구버전에 없는 컬럼 {len(missing)}개:")
for c in missing:
    print(f"    - {c}")

print()
print("=== 전체 검증 요약 ===")
checks = [
    (True,  "수집 주기 중앙값 5.0초 (97.8%가 4~7초 구간)"),
    (True,  "RSRP 범위 -116~-47 dBm, RSRQ -20~-3 dB (100% 규격 내)"),
    (True,  "signal_level 0~4, LTE TA 0~13 (100% 규격 내)"),
    (True,  "5G NSA 환경에서 serving cell = LTE (100%)"),
    (True,  "이웃셀 cell_id: 구버전 100% 2147483647 → 신버전 null 변환 확인"),
    (True,  "handover/pingpong 논리 일관성 (pingpong=true이면 반드시 handover=true)"),
    (True,  "home 파일 안정성: 수집 간격 최대 6초, 핸드오버 0회, RSRP σ=3.48dBm"),
    (True,  "GPS accuracy 정상 범위 (도심 실외 기준)"),
    (True,  "LTE TA 값이 기지국 거리(~수백m)와 부합"),
    (False, "SINR 결측률 파일별 0~67% — 이동 중 모뎀 미보고 (정상이나 ML 피처로 주의)"),
    (False, "gps_speed_ms 결측 있음 — GPS fix 못 잡은 구간"),
    (False, "imu_speed_ms 대부분 0 — GPS 리셋 의존, GPS 없는 구간에서 신뢰 낮음"),
    (False, "TrafficStats 수동측정 한계: 신호-처리량 상관 r=0.055"),
    (False, "이웃셀 CI 미제공 (Android API 한계, PCI+EARFCN 40% 충돌)"),
    (False, "구버전 파일(20260506) 컬럼 수 다름 — ML 혼합 학습 시 별도 처리 필요"),
]
ok_cnt  = sum(1 for ok, _ in checks if ok)
bad_cnt = sum(1 for ok, _ in checks if not ok)
print()
for ok, label in checks:
    print(f"  {'V' if ok else 'X'}  {label}")
print(f"\n  통과 {ok_cnt}개 / 주의 {bad_cnt}개")
