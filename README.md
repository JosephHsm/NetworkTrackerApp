# NetworkTrackerApp

Android 기기에서 **LTE / 5G NSA / 5G SA** 무선망 데이터를 실시간으로 수집하고 CSV 파일로 저장하는 앱입니다.  
GPS 위치, 기지국 신호 지표, 이웃 셀 정보, 처리량을 **5초 주기 + 핸드오버 이벤트 트리거**로 기록하며, 핸드오버·커버리지 분석 및 AI/ML 학습 목적으로 활용할 수 있습니다.

---

## 목차

1. [앱 개요](#앱-개요)
2. [시스템 요구 사항](#시스템-요구-사항)
3. [사용한 Android API](#사용한-android-api)
4. [데이터 필드 설명](#데이터-필드-설명)
5. [neighbors_json 구조](#neighbors_json-구조)
6. [실제 수집 데이터 현황](#실제-수집-데이터-현황)
7. [프로젝트 구조](#프로젝트-구조)
8. [권한 목록](#권한-목록)
9. [빌드 및 실행](#빌드-및-실행)

---

## 앱 개요

| 항목 | 내용 |
|------|------|
| 수집 주기 | 5초 주기 + 핸드오버 감지 시 즉시 추가 수집 (API 31+) |
| 저장 위치 | `Android/data/com.networktracker/files/Documents/network_log_YYYYMMDD_HHmmss_<activity>.csv` |
| 지원 RAT | GSM / UMTS(3G) / LTE(4G) / NR(5G SA) / NR-NSA |
| 최소 Android | API 29 (Android 10) |
| 데이터 안전성 | 30레코드(약 2.5분)마다 디스크에 flush — 강제 종료 시 최대 2.5분치만 유실 |

세션 시작 시 헤더가 즉시 파일에 기록되며, 이후 30레코드마다 append 저장됩니다.  
저장된 파일은 앱 내 목록에서 탭하면 타 앱(이메일, 클라우드 등)으로 공유할 수 있습니다.

### 수집 트리거

| 트리거 | 조건 | `collect_trigger` 값 |
|--------|------|----------------------|
| 주기 수집 | 5초마다 | `periodic` |
| 핸드오버 이벤트 | 서빙셀 ID 변경 감지 시 즉시 (API 31+, 직전 수집으로부터 1초 이상 경과 시) | `handover` |

---

## 시스템 요구 사항

- Android **10 (API 29)** 이상
- 권장: Android **12 (API 31)** 이상 — 주파수 밴드 정보(`serving_band`) 수집 + 핸드오버 이벤트 즉시 감지 가능
- GPS 및 모바일 데이터 연결 환경

---

## 사용한 Android API

### 1. `TelephonyManager`

| 메서드 / 속성 | 용도 |
|--------------|------|
| `tel.dataNetworkType` | 현재 데이터 연결의 RAT(Radio Access Technology) 조회 (LTE, NR 등) |
| `tel.allCellInfo` | 서빙 셀 + 이웃 셀 목록 조회 (`CellInfoLte`, `CellInfoNr` 등 포함) |
| `tel.requestCellInfoUpdate()` | API 29+: 모뎀에 셀 정보 갱신 요청 (tick 후 호출 → 다음 수집에서 신선한 데이터 확보) |
| `tel.registerTelephonyCallback()` | API 31+: `TelephonyDisplayInfo` + `CellInfo` 변경 콜백 등록 |
| `tel.listen(PhoneStateListener, ...)` | API 30: `TelephonyDisplayInfo` 변경 리스너 (하위 호환) |

### 2. `CellInfo` 서브클래스

| 클래스 | 의미 |
|--------|------|
| `CellInfoLte` | LTE(4G) 셀 정보. `CellIdentityLte` + `CellSignalStrengthLte` 포함 |
| `CellInfoNr` | NR(5G) 셀 정보. `CellIdentityNr` + `CellSignalStrengthNr` 포함 |
| `CellInfoWcdma` | WCDMA(3G) 셀 — 이웃 셀로만 기록 |
| `CellInfoGsm` | GSM(2G) 셀 — 이웃 셀로만 기록 |

#### `CellIdentityLte` 주요 필드
| 필드 | 의미 |
|------|------|
| `.ci` | Cell Identity (28-bit, 기지국 고유 ID). `eNB_ID = CI >> 8`, `셀인덱스 = CI & 0xFF` |
| `.pci` | Physical Cell ID (0–503) |
| `.earfcn` | E-UTRA Absolute Radio Frequency Channel Number |
| `.tac` | Tracking Area Code |
| `.mccString` / `.mncString` | 사업자 코드 |
| `.bands` *(API 31+)* | 주파수 밴드 번호 배열 |

#### `CellSignalStrengthLte` 주요 필드
| 필드 | 의미 | 단위 |
|------|------|------|
| `.rsrp` | Reference Signal Received Power | dBm |
| `.rsrq` | Reference Signal Received Quality | dB |
| `.rssi` | Received Signal Strength Indicator | dBm |
| `.rssnr` | Reference Signal SNR — **모뎀 미보고 시 UNAVAILABLE** | dB |
| `.timingAdvance` | 기지국까지의 거리 추정 (1단위 ≈ 78 m) | — |
| `.level` | 신호 강도 단계 (0~4) | — |

#### `CellIdentityNr` 주요 필드
| 필드 | 의미 |
|------|------|
| `.nci` | NR Cell Identity (36-bit Long) |
| `.pci` | Physical Cell ID |
| `.nrarfcn` | NR-ARFCN (NR 채널 번호) |
| `.tac` | Tracking Area Code |
| `.bands` *(API 31+)* | NR 밴드 배열 |

#### `CellSignalStrengthNr` 주요 필드
| 필드 | 의미 | 단위 |
|------|------|------|
| `.ssRsrp` | SS-RSRP (Synchronization Signal RSRP) | dBm |
| `.ssRsrq` | SS-RSRQ | dB |
| `.ssSinr` | SS-SINR | dB |
| `.csiRsrp` | CSI-RSRP (채널 상태 정보 기반) | dBm |
| `.csiRsrq` | CSI-RSRQ | dB |
| `.csiSinr` | CSI-SINR | dB |

> **NSA(5G Non-Standalone) 환경 주의:** NSA에서는 LTE가 앵커(serving cell)이므로 `serving_cell_id`는 LTE CI만 기록됩니다. NCI(NR Cell Identity)가 서빙셀로 기록되는 경우는 NR SA 환경(`is_5g_actual=true`)에서만 발생합니다.

### 3. `TelephonyDisplayInfo` (API 30+)

`overrideNetworkType`을 통해 **5G NSA** 연결 여부를 감지합니다.

| 상수 | 의미 |
|------|------|
| `OVERRIDE_NETWORK_TYPE_NR_NSA` | 5G NSA (LTE 앵커, NR 데이터) |
| `OVERRIDE_NETWORK_TYPE_NR_ADVANCED` | 5G NR Advanced (mmWave 등 고속) |
| `OVERRIDE_NETWORK_TYPE_LTE_CA` | LTE Carrier Aggregation |

### 4. `LocationManager`

| 메서드 | 용도 |
|--------|------|
| `lm.requestLocationUpdates(GPS_PROVIDER, 2000, 1f, listener)` | 2초 / 1m 간격으로 GPS 위치 수신 |
| `lm.requestLocationUpdates(NETWORK_PROVIDER, 2000, 1f, listener)` | 네트워크 기반 위치 (GPS 보완용) |
| `lm.getLastKnownLocation(provider)` | 마지막 알려진 위치 (초기 위치 빠른 확보) |

### 5. `TrafficStats`

| 메서드 | 용도 |
|--------|------|
| `TrafficStats.getMobileRxBytes()` | 부팅 이후 **셀룰러 전용** 수신 바이트 (신호↔처리량 분석에 사용) |
| `TrafficStats.getMobileTxBytes()` | 부팅 이후 **셀룰러 전용** 송신 바이트 |
| `TrafficStats.getTotalRxBytes()` | 부팅 이후 전체(Wi-Fi+셀룰러) 수신 바이트 (참고용) |
| `TrafficStats.getTotalTxBytes()` | 부팅 이후 전체 송신 바이트 (참고용) |

이전 호출과의 차분(Δ bytes / Δ time)으로 순간 처리량(Bps)을 계산합니다.  
`getMobileRxBytes()`가 미지원 단말에서는 `-1`을 반환하며, 이 경우 `mobile_rx_speed_Bps = 0`으로 기록됩니다.

### 6. `SensorManager` (IMU)

`TYPE_LINEAR_ACCELERATION` 센서로 수평 가속도를 적분해 이동 속도를 추정합니다.  
GPS 속도가 가용할 때마다 GPS 값으로 리셋하여 drift를 억제합니다.

### 7. `ConnectivityManager`

`NetworkCapabilities.TRANSPORT_WIFI`로 Wi-Fi 활성 여부를 감지합니다 (`wifi_active` 컬럼).

---

## 데이터 필드 설명

현재 CSV 포맷은 **61개 컬럼**입니다. `*` 표시는 이번 버전에서 추가된 컬럼입니다.

### 타임스탬프 + 활동 태그

| 필드명 | 타입 | 설명 | 예시 값 |
|--------|------|------|---------|
| `timestamp` | Long | Unix 타임스탬프 (밀리초) | `1715123456789` |
| `datetime` | String | 사람이 읽기 쉬운 날짜/시각 | `2026-05-12 12:34:12` |
| `activity` * | String | 수집 활동 태그 (UI 선택) | `walking`, `subway`, `car`, `home`, `unknown` |

### GPS 위치

| 필드명 | 타입 | 설명 | 예시 값 |
|--------|------|------|---------|
| `latitude` | Double | 위도 (WGS84) | `37.501234` |
| `longitude` | Double | 경도 (WGS84) | `127.023456` |
| `gps_accuracy_m` | Float | 수평 정확도 (반경, 미터) | `5.2` |
| `gps_speed_ms` | Float | GPS 이동 속도 (m/s). 미제공 시 공백 | `1.4` |
| `gps_bearing_deg` | Float | 이동 방향 (북쪽=0°, 시계방향) | `270.0` |
| `gps_altitude_m` | Double | 해발 고도 (미터) | `48.0` |

### 네트워크 종류 및 5G 감지

| 필드명 | 타입 | 설명 | 예시 값 |
|--------|------|------|---------|
| `network_type` | String | `dataNetworkType` 기반 RAT | `LTE`, `NR(5G)`, `UNKNOWN` |
| `override_network_type` | String | `TelephonyDisplayInfo` 오버라이드 값 | `NR_NSA`, `NR_ADVANCED`, `LTE_CA`, `NONE` |
| `generation` | String | 세대 구분 (앱에서 산출) | `4G`, `5G`, `3G`, `2G` |
| `is_5g` | Boolean | SA·NSA·NR 서빙셀 중 하나라도 true이면 true | `true` |
| `is_5g_actual` | Boolean | NR SA (`NETWORK_TYPE_NR`) 로 연결된 경우 | `false` |
| `is_5g_display` | Boolean | NSA/NR_ADVANCED 표시 중인 경우 | `true` |
| `nr_cell_seen` | Boolean | allCellInfo에서 CellInfoNr 존재 여부 (서빙+이웃 포함) | `true` |
| `nr_serving_cell_seen` | Boolean | NR 셀이 서빙 셀(`isRegistered=true`)인 경우 | `false` |

> `is_5g_display=true` + `is_5g_actual=false` → **5G NSA** (LTE 앵커, NR 보조)  
> `is_5g_actual=true` → **5G SA** (독립 NR 연결). NSA 환경에서는 `nr_serving_cell_seen`이 거의 항상 false입니다.

### 서빙 셀 식별자

| 필드명 | 타입 | 설명 | 예시 값 |
|--------|------|------|---------|
| `serving_cell_id` | String | LTE: CI(28-bit) / NR: NCI(36-bit) | `52543745` |
| `serving_pci` | Int | Physical Cell ID (LTE: 0–503, NR: 0–1007) | `272` |
| `serving_freq_arfcn` | Int | EARFCN(LTE) 또는 NR-ARFCN(NR) | `100` (LTE B1) |
| `serving_band` | String | 주파수 밴드 번호 (API 31+만 수집, 이하 공백) | `1` (LTE B1), `78` (NR n78) |
| `serving_tac` | Int | Tracking Area Code | `8282` |
| `mcc` | String | Mobile Country Code | `450` (한국) |
| `mnc` | String | Mobile Network Code | `05` (SKT), `06` (LG U+), `08` (KT) |

### 신호 강도 (LTE / NR 공통)

| 필드명 | 단위 | 설명 | 실측 범위 |
|--------|------|------|-----------|
| `rsrp_dbm` | dBm | Reference Signal Received Power | −116 ~ −47 (−80 이상: 양호) |
| `rsrq_db` | dB | Reference Signal Received Quality | −20 ~ −3 (−10 이상: 양호) |
| `rssi_dbm` | dBm | Received Signal Strength Indicator (LTE 전용) | −110 ~ −40 |
| `sinr_snr_db` | dB | RS-SNR (LTE) / SS-SINR (NR). **모뎀 미보고 시 공백** | −9 ~ 30 |
| `signal_level` | — | Android 신호 강도 단계 (0=없음, 4=최상) | 0 ~ 4 |

> `sinr_snr_db` 결측률은 단말·기지국 조합에 따라 0~67%까지 크게 다릅니다. 결측값은 0이 아닌 NaN으로 처리해야 합니다.

### LTE 전용 측정값

| 필드명 | 단위 | 설명 | 예시 값 |
|--------|------|------|---------|
| `timing_advance_lte` | — | Timing Advance (0–1282). 1단위 ≈ 78 m로 기지국 거리 추정 | `4` (~312 m) |

### 5G NR CSI 측정값

`CellInfoNr` 서빙 셀이 있을 때만 수집됩니다. NSA 환경에서는 LTE가 서빙 셀이므로 **항상 공백**입니다.

| 필드명 | 단위 | 설명 |
|--------|------|------|
| `csi_rsrp_dbm` | dBm | CSI-RSRP — 채널 상태 정보 기반 수신 전력 |
| `csi_rsrq_db` | dB | CSI-RSRQ |
| `csi_sinr_db` | dB | CSI-SINR |

### 처리량

| 필드명 | 단위 | 설명 |
|--------|------|------|
| `rx_speed_Bps` | B/s | 전체(Wi-Fi+셀룰러) 수신 처리량 — 참고용 |
| `tx_speed_Bps` | B/s | 전체 송신 처리량 — 참고용 |
| `rx_bitrate_Mbps` | Mbps | rx_speed_Bps × 8 / 1,000,000 |
| `tx_bitrate_Mbps` | Mbps | tx_speed_Bps × 8 / 1,000,000 |
| `mobile_rx_speed_Bps` * | B/s | **셀룰러 전용** 수신 처리량 (`getMobileRxBytes` 기반) |
| `mobile_tx_speed_Bps` * | B/s | 셀룰러 전용 송신 처리량 |
| `mobile_rx_bitrate_Mbps` * | Mbps | 셀룰러 전용 Rx Mbps — 신호↔처리량 분석에 사용 |
| `mobile_tx_bitrate_Mbps` * | Mbps | 셀룰러 전용 Tx Mbps |
| `wifi_active` * | Boolean | Wi-Fi 연결 여부 — true이면 total rx/tx가 오염됨 |

### 이웃 셀 요약

| 필드명 | 타입 | 설명 |
|--------|------|------|
| `neighbor_count` | Int | 전체 이웃 셀 수 |
| `nr_neighbor_count` | Int | NR(5G) 이웃 셀 수 |
| `lte_neighbor_count` | Int | LTE(4G) 이웃 셀 수 |
| `best_nbr_rsrp_dbm` * | Int | 이웃셀 최강 RSRP — JSON 파싱 없이 ML 피처로 사용 |
| `best_nbr_pci` * | Int | 최강 이웃셀의 PCI |
| `best_nbr_arfcn` * | Int | 최강 이웃셀의 EARFCN / NR-ARFCN |

### IMU 속도

| 필드명 | 단위 | 설명 |
|--------|------|------|
| `imu_speed_ms` | m/s | 가속도 센서 적분 속도. GPS 가용 시 GPS 속도로 리셋됨. GPS 불가 구간에서는 drift 누적 가능 |

### 핸드오버 감지

| 필드명 | 타입 | 설명 |
|--------|------|------|
| `handover_detected` | Boolean | 직전 샘플 대비 서빙셀 ID 변경 발생 |
| `ping_pong_detected` | Boolean | 핸드오버 후 30초 이내 이전 셀로 복귀 |
| `collect_trigger` * | String | 이 행이 수집된 원인: `periodic`(5초 주기) / `handover`(이벤트) |
| `cell_duration_s` * | Long | 현재 셀에 머문 시간 (초) — HO timing 예측 피처 |
| `ho_count_30s` * | Int | 최근 30초 핸드오버 횟수 — 이동성 지표 |
| `rsrp_delta` * | Int | RSRP 변화량 (이전 샘플 대비, 핸드오버 직후는 공백) |
| `sinr_delta` * | Int | SINR 변화량 (이전 샘플 대비, 핸드오버 직후는 공백) |
| `cell_info_age_ms` * | Long | allCellInfo 데이터 나이 (ms) — 8000 이상이면 stale 가능 |
| `prev_serving_cell_id` * | String | 핸드오버 직전 서빙셀 ID |
| `prev_rsrp_dbm` * | Int | 핸드오버 직전 RSRP — A3 이벤트 분석용 |
| `prev_rsrq_db` * | Int | 핸드오버 직전 RSRQ |

### 이웃 셀 상세

| 필드명 | 타입 | 설명 |
|--------|------|------|
| `neighbors_json` | JSON Array | 이웃 셀 상세 정보. **RSRP 내림차순 정렬**됨 |

---

## neighbors_json 구조

이웃 셀은 **RSRP 내림차순으로 정렬**되어 저장됩니다. 첫 번째 항목이 가장 강한 신호입니다.

### LTE 이웃 셀

```json
{
  "type":    "LTE",
  "cell_id": null,
  "pci":     273,
  "earfcn":  2600,
  "tac":     null,
  "rsrp":    -75,
  "rsrq":    -13,
  "rssi":    -53,
  "snr":     null,
  "ta":      null,
  "level":   4,
  "bands":   "5"
}
```

> `cell_id`, `tac`, `snr`, `ta` 등은 Android 모뎀이 이웃 셀에 대해 미제공하므로 **`null`이 정상**입니다.

**이웃셀 식별의 한계:**  
PCI는 0–503으로만 정의되며, 인접하지 않은 원거리 기지국끼리 동일한 PCI를 재사용합니다.  
실측 데이터 분석 결과, PCI+EARFCN 조합 260개 중 **104개(40%)가 서로 다른 eNB에서 충돌**했습니다 (예: PCI=485, EARFCN=100 → 7개 다른 eNB에서 사용).  
따라서 PCI+EARFCN 조합은 **단일 세션의 로컬 범위(수 km 이내)에서만 임시 구별 수단**으로 쓸 수 있으며, 세션을 넘나들거나 넓은 지역에 걸친 이웃셀 추적에는 사용할 수 없습니다.  
이웃셀의 전역 고유 식별은 현재 Android API로는 불가능합니다.

### NR(5G) 이웃 셀

```json
{
  "type":     "NR(5G)",
  "nci":      null,
  "pci":      500,
  "arfcn":    630048,
  "tac":      null,
  "ss_rsrp":  -88,
  "ss_rsrq":  -10,
  "ss_sinr":  18,
  "csi_rsrp": -90,
  "csi_rsrq": -11,
  "csi_sinr": 16,
  "level":    3,
  "bands":    "78"
}
```

### WCDMA(3G) 이웃 셀

```json
{
  "type":   "WCDMA",
  "cid":    12345,
  "psc":    200,
  "uarfcn": 10688,
  "dbm":    -85,
  "level":  2
}
```

---

## 실제 수집 데이터 현황

2026년 5월 수집 데이터 기준 (총 5,910행, 11개 세션).

### 세션 목록

| 파일명 | 활동 | 행수 | 수집시간 | 수집 위치 | 5G표시비율 |
|--------|------|------|----------|-----------|-----------|
| `20260506_075510_unknown` | 불명 (오전) | 515 | 43분 | 37.568N, 127.122E | 0% |
| `20260506_163853_unknown` | 불명 (오후) | 507 | 42분 | 37.572N, 127.130E | 0% |
| `20260512_123412_walking` | 도보 이동 | 118 | 10분 | 성동구 37.547N | 74% |
| `20260512_125212_subway` | 지하철 | 876 | 73분 | 37.523N, 127.047E | 100% |
| `20260512_160738_subway` | 지하철 | 1,045 | 87분 | 37.511N, 126.829E | 99% |
| `20260517_160607_subway` | 지하철 | 802 | 67분 | 37.562N, 126.973E | 86% |
| `20260524_123419_car` | 차량 이동 | 445 | 37분 | 37.523N, 126.621E | 71% |
| `20260524_163715_subway` | 지하철 | 849 | 71분 | 37.499N, 126.905E | 95% |
| `20260526_125145_subway` | 지하철 | 371 | 24분 | 37.547N, 127.047E | 100% |
| `20260527_131649_walking` | 도보 이동 | 193 | 15분 | 37.550N, 127.073E | 91% |
| `20260529_135952_home` | 실내 고정 | 189 | 16분 | 37.551N, 127.075E | 100% |

> `unknown`으로 표시된 두 파일은 구버전 앱으로 수집되어 컬럼 수가 적습니다 (신호 측정값은 동일).

### 주요 실측 통계

| 지표 | 값 |
|------|---|
| 전체 수집 주기 중앙값 | 5.0초 (97.8%가 4~7초 구간) |
| 평균 RSRP | -85.0 dBm |
| SINR 결측률 | 19.8% (파일별 0~67%) |
| 유니크 LTE CI 수 | 519개 (eNB 134개) |
| 핸드오버 총 횟수 | 1,009건 (intra-eNB 67%, inter-eNB 33%) |
| 핑퐁 핸드오버 | 82건 (30초 이내 복귀) |
| 활동별 핸드오버 빈도 | subway: 2.86회/분, car: 2.05회/분, walking: 0.48회/분, home: 0회 |
| 주 주파수 밴드 | Band 1(2100MHz) 54%, Band 7(2600MHz) 44% |

---

## 프로젝트 구조

```
app/src/main/java/com/networktracker/
├── data/
│   └── NetworkRecord.kt        # 수집 데이터 모델 + CSV 직렬화 (61컬럼)
├── collector/
│   └── NetworkDataCollector.kt # Android API 호출, 데이터 수집, 핸드오버 감지
├── logger/
│   └── CsvLogger.kt            # CSV 세션 관리 (30레코드마다 flush)
├── service/
│   └── NetworkLoggingService.kt# Foreground Service — 주기 수집 + 이벤트 수집
└── ui/
    └── MainActivity.kt         # UI, 활동 태그 선택, 권한 요청, 파일 공유
```

---

## 권한 목록

| 권한 | 용도 |
|------|------|
| `ACCESS_FINE_LOCATION` | GPS 위치 수신 + `allCellInfo()` 호출 (API 29+에서 필수) |
| `ACCESS_COARSE_LOCATION` | 네트워크 기반 위치 보완 |
| `READ_PHONE_STATE` | `TelephonyManager` 통해 셀 정보 및 신호 강도 조회 |
| `ACCESS_NETWORK_STATE` | Wi-Fi 연결 여부 감지 (`ConnectivityManager`) |
| `FOREGROUND_SERVICE` | 백그라운드 지속 수집을 위한 Foreground Service 실행 |
| `FOREGROUND_SERVICE_LOCATION` | Foreground Service에서 위치 접근 (API 29+) |
| `POST_NOTIFICATIONS` | 상태 알림 표시 (Android 13+, API 33+) |

---

## 빌드 및 실행

```bash
# Android Studio에서 열기 후 빌드
./gradlew assembleDebug

# 기기에 설치
adb install app/build/outputs/apk/debug/app-debug.apk
```

**요구 환경:**
- Android Studio Hedgehog 이상
- JDK 8 이상
- 물리 기기 권장 (에뮬레이터는 셀 정보 미지원)

**배터리 최적화 해제 권장:** 설정 → 앱 → NetworkTrackerApp → 배터리 → "제한 없음"  
설정하지 않으면 Doze Mode에서 Foreground Service가 제한될 수 있습니다.
