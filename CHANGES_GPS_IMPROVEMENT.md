# GPS 보완 개선 내역

> 작성일: 2026-06-07

---

## 배경

지하철 진입 시 GPS 위성 신호가 차단되면서 `latitude` / `longitude` 값이 진입 직전 지점에 동결되는 문제가 있었다.
이웃셀 CI 미제공 문제(ISSUE_ANALYSIS.md 이슈 7·9)는 Android 모뎀 한계로 소프트웨어 해결이 불가능하므로,
위치 정보 품질을 높이고 stale 여부를 데이터로 추적하는 방향으로 개선했다.

---

## 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/build.gradle` | `play-services-location:21.3.0` 의존성 추가 |
| `app/.../collector/NetworkDataCollector.kt` | FusedLocationProviderClient 도입, 위치 소스·나이 추적 |
| `app/.../data/NetworkRecord.kt` | `locationSource`, `locationAgeS` 필드 및 CSV 컬럼 추가 |

---

## 핵심 변경: FusedLocationProviderClient 도입

### 이전

`LocationManager`에 `GPS_PROVIDER`와 `NETWORK_PROVIDER`를 직접 등록했다.
GPS 신호가 끊기면 OS가 대안 소스로 전환하는 로직이 없어서 좌표가 동결됐다.

### 이후

Google Play Services의 `FusedLocationProviderClient`를 주 소스로 사용한다.
OS가 GPS·Wi-Fi 포지셔닝·기지국 위치를 자동으로 융합해 가장 정확한 위치를 제공한다.
지하 역사 내 Wi-Fi AP 신호가 잡히면 GPS 없이도 위치가 계속 갱신된다.

```
GPS 신호 있음  → GPS 좌표 (정밀도 ~5–20 m)
GPS 없음, WiFi → WiFi 포지셔닝 (정밀도 ~50–200 m)
GPS·WiFi 없음  → 기지국 삼각측량 (정밀도 ~300–1000 m)
```

GMS가 없는 기기에서는 기존 `LocationManager` 방식으로 자동 폴백한다.

### 코드 구조

```kotlin
// startLocationUpdates() 흐름
1. getLastKnownLocation()으로 시작 직후 좌표 즉시 확보
2. fusedClient.requestLocationUpdates() 시작 (성공 시 usingFused = true)
   └─ onFailure → LocationManager GPS/NETWORK 폴백
3. locationCallback.onLocationResult() 콜백마다
   lastLocation, locationProvider("fused"), locationTimeMs 갱신

// stopLocationUpdates() 흐름
usingFused == true → fusedClient.removeLocationUpdates()
항상             → lm.removeUpdates() (폴백 리스너 정리)
```

---

## 신규 CSV 컬럼 (2개 추가 → 총 63개)

컬럼 위치: `gps_altitude_m` 바로 뒤, `network_type` 앞

| 컬럼명 | 타입 | 값 예시 | 설명 |
|--------|------|---------|------|
| `location_source` | string | `fused` | 위치를 어떻게 얻었는지 (아래 참고) |
| `location_age_s` | int / 공백 | `3` | 수집 시점 기준 위치 정보 나이 (초) |

### `location_source` 값 정의

| 값 | 의미 |
|----|------|
| `fused` | FusedLocationProviderClient가 60초 이내 갱신한 위치 (GPS·WiFi·기지국 융합) |
| `gps` | LocationManager GPS_PROVIDER 폴백 (60초 이내) |
| `network` | LocationManager NETWORK_PROVIDER 폴백 (60초 이내) |
| `stale_fused` | Fused 위치이나 60초 이상 갱신 없음 — 지하 GPS 동결 구간 |
| `stale_gps` | GPS 위치이나 60초 이상 갱신 없음 |
| `stale_network` | Network 위치이나 60초 이상 갱신 없음 |
| `none` | 위치 정보 없음 |

---

## 데이터 분석 시 활용 방법

```python
# 지하 GPS 동결 구간 필터링
df_valid = df[~df['location_source'].str.startswith('stale')]

# 위치 소스별 정밀도 분포 확인
df.groupby('location_source')['gps_accuracy_m'].describe()

# Fused가 WiFi 수준으로 떨어진 구간 (지하 진입 추정)
df_underground = df[(df['location_source'] == 'fused') & (df['gps_accuracy_m'] > 100)]
```

---

## 주의사항

- `location_age_s`가 크고 `location_source`가 `stale_*`인 행은 위치 신뢰도가 낮다. 이웃셀·핸드오버 분석에서 지상/지하 구간을 분리할 때 이 컬럼을 기준으로 사용할 수 있다.
- `gps_accuracy_m`이 크다고 반드시 stale은 아니다 (Wi-Fi 기반 fused도 accuracy가 낮게 나옴). 두 컬럼을 함께 봐야 한다.
- 이웃셀 CI 미제공 문제는 이번 개선과 무관하며 Android API 한계로 해결 불가하다 (ISSUE_ANALYSIS.md 이슈 7·9 참고).
