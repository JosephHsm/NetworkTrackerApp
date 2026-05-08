# NetworkTrackerApp

Android 기기에서 **LTE / 5G NSA / 5G SA** 무선망 데이터를 실시간으로 수집하고 CSV 파일로 저장하는 앱입니다.  
GPS 위치, 기지국 신호 지표, 이웃 셀 정보, 처리량을 5초마다 기록하며, 핸드오버·커버리지 분석 등의 연구 목적으로 활용할 수 있습니다.

---

## 목차

1. [앱 개요](#앱-개요)
2. [시스템 요구 사항](#시스템-요구-사항)
3. [사용한 Android API](#사용한-android-api)
4. [데이터 필드 설명](#데이터-필드-설명)
5. [neighbors_json 구조](#neighbors_json-구조)
6. [실제 예시 데이터](#실제-예시-데이터)
7. [프로젝트 구조](#프로젝트-구조)
8. [권한 목록](#권한-목록)
9. [빌드 및 실행](#빌드-및-실행)

---

## 앱 개요

| 항목 | 내용 |
|------|------|
| 수집 주기 | 5초 (기본값, `EXTRA_INTERVAL`로 변경 가능) |
| 저장 위치 | `Android/data/com.networktracker/files/Documents/network_log_YYYYMMDD_HHmmss.csv` |
| 지원 RAT | GSM / UMTS(3G) / LTE(4G) / NR(5G SA) / NR-NSA |
| 최소 Android | API 29 (Android 10) |

로깅 시작 시 CSV 헤더가 기록되고, 중지 시 누적 데이터가 한 번에 파일로 저장됩니다.  
저장된 파일은 앱 내 목록에서 탭하면 타 앱(이메일, 클라우드 등)으로 공유할 수 있습니다.

---

## 시스템 요구 사항

- Android **10 (API 29)** 이상
- 권장: Android **12 (API 31)** 이상 — 주파수 밴드 정보(`serving_band`) 수집 가능
- GPS 및 모바일 데이터 연결 환경

---

## 사용한 Android API

### 1. `TelephonyManager`

| 메서드 / 속성 | 용도 |
|--------------|------|
| `tel.dataNetworkType` | 현재 데이터 연결의 RAT(Radio Access Technology) 조회 (LTE, NR 등) |
| `tel.allCellInfo` | 서빙 셀 + 이웃 셀 목록 조회 (`CellInfoLte`, `CellInfoNr` 등 포함) |
| `tel.registerTelephonyCallback()` | API 31+: `TelephonyDisplayInfo` 변경 콜백 등록 (5G NSA 감지) |
| `tel.listen(PhoneStateListener, LISTEN_DISPLAY_INFO_CHANGED)` | API 30: `TelephonyDisplayInfo` 변경 리스너 등록 (구형 방식) |

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
| `.ci` | Cell Identity (기지국 고유 ID) |
| `.pci` | Physical Cell ID (0–503) |
| `.earfcn` | E-UTRA Absolute Radio Frequency Channel Number (채널 번호) |
| `.tac` | Tracking Area Code |
| `.mccString` / `.mncString` | 사업자 코드 |
| `.bands` *(API 31+)* | 주파수 밴드 번호 배열 |

#### `CellSignalStrengthLte` 주요 필드
| 필드 | 의미 | 단위 |
|------|------|------|
| `.rsrp` | Reference Signal Received Power | dBm |
| `.rsrq` | Reference Signal Received Quality | dB |
| `.rssi` | Received Signal Strength Indicator | dBm |
| `.rssnr` | Reference Signal SNR | dB |
| `.timingAdvance` | 기지국까지의 거리 추정 (1단위 ≈ 78 m) | — |
| `.level` | 신호 강도 단계 (0~4) | — |

#### `CellIdentityNr` 주요 필드
| 필드 | 의미 |
|------|------|
| `.nci` | NR Cell Identity |
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
| `TrafficStats.getTotalRxBytes()` | 기기 부팅 이후 총 수신 바이트 |
| `TrafficStats.getTotalTxBytes()` | 기기 부팅 이후 총 송신 바이트 |

이전 호출과의 차분(Δ bytes / Δ time)으로 순간 처리량(Bps)을 계산합니다.

---

## 데이터 필드 설명

CSV 파일의 열 순서 및 의미입니다.

### 타임스탬프

| 필드명 | 타입 | 설명 | 예시 값 |
|--------|------|------|---------|
| `timestamp` | Long | Unix 타임스탬프 (밀리초) | `1715123456789` |
| `datetime` | String | 사람이 읽기 쉬운 날짜/시각 | `2024-05-08 14:30:56` |

### GPS 위치

| 필드명 | 타입 | 설명 | 예시 값 |
|--------|------|------|---------|
| `latitude` | Double | 위도 (WGS84) | `37.501234` |
| `longitude` | Double | 경도 (WGS84) | `127.023456` |
| `gps_accuracy_m` | Float | 수평 정확도 (반경, 미터) | `5.2` |
| `gps_speed_ms` | Float | 이동 속도 (m/s). 정지 시 0 또는 공백 | `1.4` |
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
> `is_5g_actual=true` → **5G SA** (독립 NR 연결)

### 서빙 셀 식별자

| 필드명 | 타입 | 설명 | 예시 값 |
|--------|------|------|---------|
| `serving_cell_id` | String | LTE: CI / NR: NCI | `123456789` |
| `serving_pci` | Int | Physical Cell ID (LTE: 0–503, NR: 0–1007) | `234` |
| `serving_freq_arfcn` | Int | EARFCN(LTE) 또는 NR-ARFCN(NR) — 채널 번호 | `1300` (LTE B1) |
| `serving_band` | String | 주파수 밴드 번호 (API 31+만 수집, 이하 공백) | `1` (LTE B1), `78` (NR n78) |
| `serving_tac` | Int | Tracking Area Code | `5120` |
| `mcc` | String | Mobile Country Code | `450` (한국) |
| `mnc` | String | Mobile Network Code | `05` (SKT), `06` (LG U+), `08` (KT) |

### 신호 강도 (LTE / NR 공통)

| 필드명 | 단위 | 설명 | 일반적 범위 |
|--------|------|------|------------|
| `rsrp_dbm` | dBm | Reference Signal Received Power — 신호 세기 | −140 ~ −44 (−80 이상: 양호) |
| `rsrq_db` | dB | Reference Signal Received Quality — 간섭 포함 품질 | −20 ~ −3 (−10 이상: 양호) |
| `rssi_dbm` | dBm | Received Signal Strength Indicator (LTE 전용) | −110 ~ −40 |
| `sinr_snr_db` | dB | RS-SNR (LTE) / SS-SINR (NR) | −20 ~ 30 (0 이상: 양호) |
| `signal_level` | — | Android 신호 강도 단계 (0=없음, 4=최상) | `0` ~ `4` |

### LTE 전용 측정값

| 필드명 | 단위 | 설명 | 예시 값 |
|--------|------|------|---------|
| `timing_advance_lte` | — | Timing Advance (0–1282). 1단위 ≈ 78 m로 기지국 거리 추정 | `4` (~312 m) |

### 5G NR CSI 측정값

빔 관리(Beam Management) 기반 채널 품질 측정값으로, `CellInfoNr` 서빙 셀이 있을 때만 수집됩니다.

| 필드명 | 단위 | 설명 | 예시 값 |
|--------|------|------|---------|
| `csi_rsrp_dbm` | dBm | CSI-RSRP — 채널 상태 정보 기반 수신 전력 | `−85` |
| `csi_rsrq_db` | dB | CSI-RSRQ | `−12` |
| `csi_sinr_db` | dB | CSI-SINR | `15` |

### 처리량 (TrafficStats 기반)

| 필드명 | 단위 | 설명 | 예시 값 |
|--------|------|------|---------|
| `rx_speed_Bps` | Byte/s | 순간 수신 처리량 (바이트/초) | `1250000` |
| `tx_speed_Bps` | Byte/s | 순간 송신 처리량 (바이트/초) | `62500` |
| `rx_bitrate_Mbps` | Mbps | rx_speed_Bps × 8 / 1,000,000 | `10.0000` |
| `tx_bitrate_Mbps` | Mbps | tx_speed_Bps × 8 / 1,000,000 | `0.5000` |

> `TrafficStats`는 **기기 전체** 트래픽 합계이므로 Wi-Fi/셀룰러 구분 없이 합산됩니다.

### 이웃 셀 요약

| 필드명 | 타입 | 설명 | 예시 값 |
|--------|------|------|---------|
| `neighbor_count` | Int | 전체 이웃 셀 수 | `3` |
| `nr_neighbor_count` | Int | NR(5G) 이웃 셀 수 | `1` |
| `lte_neighbor_count` | Int | LTE(4G) 이웃 셀 수 | `2` |
| `neighbors_json` | JSON Array | 이웃 셀 상세 정보 (아래 구조 참조) | `[{"type":"LTE",...}]` |

---

## neighbors_json 구조

### LTE 이웃 셀

```json
{
  "type":    "LTE",
  "cell_id": "2147483647",
  "pci":     273,
  "earfcn":  2600,
  "tac":     2147483647,
  "rsrp":    -75,
  "rsrq":    -13,
  "rssi":    -53,
  "snr":     2147483647,
  "ta":      2147483647,
  "level":   4,
  "bands":   "5"
}
```

> `2147483647` = `Int.MAX_VALUE` = Android `CellInfo.UNAVAILABLE`.  
> 이웃 셀의 `cell_id`, `tac`, `snr`, `ta` 등은 기기가 제공하지 않는 경우 이 값으로 반환됩니다.

### NR(5G) 이웃 셀

```json
{
  "type":     "NR(5G)",
  "nci":      "12345678901",
  "pci":      500,
  "arfcn":    630048,
  "tac":      5120,
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

## 실제 예시 데이터

아래는 2026-05-08 실제 기기(LG U+, 서울)에서 수집한 행입니다.

```
timestamp,datetime,latitude,longitude,gps_accuracy_m,gps_speed_ms,gps_bearing_deg,gps_altitude_m,network_type,override_network_type,generation,is_5g,is_5g_actual,is_5g_display,nr_cell_seen,nr_serving_cell_seen,serving_cell_id,serving_pci,serving_freq_arfcn,serving_band,serving_tac,mcc,mnc,rsrp_dbm,rsrq_db,rssi_dbm,sinr_snr_db,signal_level,timing_advance_lte,csi_rsrp_dbm,csi_rsrq_db,csi_sinr_db,rx_speed_Bps,tx_speed_Bps,rx_bitrate_Mbps,tx_bitrate_Mbps,neighbor_count,nr_neighbor_count,lte_neighbor_count,neighbors_json

1778208302329,2026-05-08 11:45:02,37.54656443,127.06817323,5.4635096,0.0,,52.76727294921875,LTE,NR_NSA,5G,true,false,true,false,false,52543756,273,100,1,8282,450,06,-84,-13,-51,13,4,,,,,84375,17360,0.6750,0.1389,4,0,4,"[{""type"":""LTE"",""cell_id"":""2147483647"",""pci"":273,""earfcn"":2600,""tac"":2147483647,""rsrp"":-75,""rsrq"":-13,""rssi"":-53,""snr"":2147483647,""ta"":2147483647,""level"":4,""bands"":""5""},{""type"":""LTE"",""cell_id"":""2147483647"",""pci"":421,""earfcn"":2600,""tac"":2147483647,""rsrp"":-81,""rsrq"":-18,""rssi"":-53,""snr"":2147483647,""ta"":2147483647,""level"":4,""bands"":""5""},{""type"":""LTE"",""cell_id"":""2147483647"",""pci"":273,""earfcn"":3050,""tac"":2147483647,""rsrp"":-95,""rsrq"":-13,""rssi"":-61,""snr"":2147483647,""ta"":2147483647,""level"":3,""bands"":""7""},{""type"":""LTE"",""cell_id"":""2147483647"",""pci"":418,""earfcn"":3050,""tac"":2147483647,""rsrp"":-102,""rsrq"":-17,""rssi"":-73,""snr"":2147483647,""ta"":2147483647,""level"":2,""bands"":""7""}]"
```

**해석:**
- `mcc=450, mnc=06` → **LG U+** (한국)
- `LTE + NR_NSA + is_5g=true` → **5G NSA** 환경 (LTE 앵커 연결, NR 보조 데이터)
- `earfcn=100` → LTE **Band 1 (2100 MHz)**, `serving_band=1`로 확인
- `rsrp=-84 dBm` → 보통 수준의 LTE 신호 (−80 이하지만 −90 이상 → 사용 가능)
- `rsrq=-13 dB, sinr=13 dB` → 신호 품질 양호
- `signal_level=4` → Android 기준 최고 단계
- `timing_advance` 공백 → 해당 레코드에서 기기가 TA를 미제공 (`CellInfo.UNAVAILABLE`)
- `csi_rsrp/rsrq/sinr` 공백 → 서빙 NR 셀 없음 (NSA이므로 NR 셀이 `isRegistered=false`)
- `rx_bitrate=0.6750 Mbps` → 수집 시점 순간 수신 처리량
- 이웃 셀 4개: Band 5(850 MHz) × 2, Band 7(2600 MHz) × 2 — 모두 LTE

---

## 프로젝트 구조

```
app/src/main/java/com/networktracker/
├── data/
│   └── NetworkRecord.kt        # 수집 데이터 모델 + CSV 직렬화
├── collector/
│   └── NetworkDataCollector.kt # Android API 호출 및 데이터 수집 로직
├── logger/
│   └── CsvLogger.kt            # CSV 파일 세션 관리 및 저장
├── service/
│   └── NetworkLoggingService.kt# Foreground Service — 백그라운드 주기적 수집
└── ui/
    └── MainActivity.kt         # UI, 권한 요청, 파일 공유
```

---

## 권한 목록

| 권한 | 용도 |
|------|------|
| `ACCESS_FINE_LOCATION` | GPS 위치 수신 + `allCellInfo()` 호출 (API 29+에서 필수) |
| `ACCESS_COARSE_LOCATION` | 네트워크 기반 위치 보완 |
| `READ_PHONE_STATE` | `TelephonyManager` 통해 셀 정보 및 신호 강도 조회 |
| `ACCESS_NETWORK_STATE` | 네트워크 상태 확인 |
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
