# NetworkTrackerApp — 이슈 분석 및 수정 내역

> 논문 데이터 수집 앱 운용 중 발견한 문제점 정리 및 코드 수정 기록  
> 작성일: 2026-05-17

---

## 1. 사전 이슈 목록 (수집 중 발견)

코드 수정 전, 데이터를 직접 들여다보며 파악한 의문점 및 문제들.

| # | 증상 | 분류 |
|---|------|------|
| 1 | 핑퐁 핸드오버 — 언제, 왜 발생하는지 모름. RSRP 값이 크게 안 떨어졌는데도 핑퐁 | 분석 + 기능 추가 필요 |
| 2 | 가속도 센서로 속도 측정 추가 요청 | 기능 추가 |
| 3 | 데이터레이트가 갑자기 올라갈 때 수신 세기(RSRP)는 크게 안 좋아진 게 아님 | 원인 분석 필요 |
| 4 | 집에도 계속 틀어놔서 일관성 있게 찍히는지 확인 필요 | 운용 이슈 |
| 5 | `sinr_snr_db` 컬럼에 빈칸이 자주 생김 | 원인 분석 필요 |
| 6 | 서빙셀 ID와 이웃셀 ID의 자릿수가 다름. PCI·EARFCN은 같은데 왜? | 버그 + 원인 분석 |
| 7 | 이웃 셀 리스트의 `cell_id`(또는 `nci`)가 전부 동일한 값 | 버그 |
| 8 | 핸드오버 타겟 셀 선택 규칙을 모르겠음. 이웃 리스트 1위가 아닌 셀로 가거나, 목록에 없던 셀로 가기도 함 | 원인 분석 필요 |
| 9 | 이웃 리스트 내용 전반에 대한 검증 필요 | 버그 포함 |

---

## 2. 이슈별 원인 분석

### [이슈 7 / 이슈 9] 이웃셀 ID가 전부 동일

**근본 원인:** Android `getAllCellInfo()` API의 설계 한계.  
모뎀은 이웃 셀(Neighbor Cell)의 Cell Identity(CI / NCI)를 거의 제공하지 않는다.  
값을 읽으면 `CellInfo.UNAVAILABLE = Integer.MAX_VALUE = 2147483647`이 반환된다.  
기존 코드는 이 값을 필터링 없이 JSON에 그대로 저장했기 때문에 모든 이웃셀 ID가 동일하게 찍힘.

```kotlin
// 기존 (문제 코드)
put("cell_id", cell.cellIdentity.ci.toString())  // UNAVAILABLE이면 "2147483647"

// 수정 후
put("cell_id", cell.cellIdentity.ci.toJsonOrNull())  // UNAVAILABLE → null
```

**결론:** 이웃 셀의 Cell ID는 Android에서 기본적으로 미제공. `null`이 정상이며, 이웃셀 구별에는 `pci + earfcn/arfcn` 조합을 사용해야 함.

---

### [이슈 6] 서빙셀 ID vs 이웃셀 ID 자릿수 차이 (PCI/EARFCN은 동일)

**근본 원인:** LTE와 NR의 Cell Identity 비트 폭이 다름.

| 규격 | 필드 | 비트 | 최대값 | 최대 자릿수 |
|------|------|------|--------|------------|
| LTE  | CI (Cell Identity) | 28 bit | 268,435,455 | 9자리 |
| NR   | NCI (NR Cell Identity) | 36 bit | 68,719,476,735 | 11자리 |

NSA(Non-Standalone) 모드에서는 LTE 앵커셀과 NR 셀이 동시에 `isRegistered = true`로 반환될 수 있다. 코드가 `allCellInfo`를 순회하면서 마지막으로 처리된 셀이 `servingCellId`를 overwrite하기 때문에, NR 셀이 마지막이면 서빙셀 ID는 NCI(더 긴 숫자)가 된다. 이웃 LTE 셀의 CI는 더 짧은 숫자이므로 자릿수가 달라 보임.

추가로, NCI가 `UNAVAILABLE_LONG = Long.MAX_VALUE = 9223372036854775807`(19자리)일 때 그대로 저장되는 버그도 있었음. 이것도 수정됨.

**PCI/EARFCN이 같은 이유:** DSS(Dynamic Spectrum Sharing) 환경에서는 LTE와 NR이 같은 주파수 대역을 공유하므로 ARFCN과 PCI가 동일하게 나올 수 있음. 정상.

---

### [이슈 5] `sinr_snr_db` 빈칸

**원인:** 정상 동작.  
- LTE: `RS-SNR`은 모뎀이 항상 보고하지 않음. 단말·기지국 조합에 따라 미제공이 일반적.  
- NR: `SS-SINR`은 제공되는 경우가 더 많지만 역시 `UNAVAILABLE`일 수 있음.  
`valid()` 확장함수가 `UNAVAILABLE` 값을 `null`로 변환하기 때문에 CSV에는 빈 칸으로 나옴.

**대응:** 빈 칸은 "미측정"이지 "0"이 아님. 분석 시 결측값으로 처리 필요.

---

### [이슈 3] 데이터레이트 급등 / 수신 세기 변화 없음

**원인:** 정상 현상. 처리량(Throughput)과 신호 세기(RSRP)는 단순 비례 관계가 아님.

주요 원인:
1. **CA(Carrier Aggregation) 활성화** — 스케줄러가 추가 캐리어를 할당하면 RSRP 변화 없이 처리량이 급등함
2. **빔포밍 / 빔 스위칭** — NR에서 더 적합한 빔으로 전환되면 처리량 향상. RSRP는 전체 평균값이라 빔 스위칭에 민감하지 않음
3. **스케줄러 우선순위** — 트래픽이 줄거나 셀 부하가 떨어지면 같은 신호 강도에서도 더 많은 RB(Resource Block) 할당 가능

---

### [이슈 8] 핸드오버 타겟 셀 선택 규칙

**원인:** UE(단말)가 선택하는 게 아니라 기지국(eNB/gNB)이 결정.

핸드오버 결정 흐름:
1. UE가 측정 보고(Measurement Report) 전송 — 이웃 셀 신호 포함
2. 기지국이 **A3 이벤트** 기준으로 핸드오버 명령 결정
   - A3 이벤트 조건: `이웃셀 RSRP > 서빙셀 RSRP + Offset + Hysteresis` 가 `Time-to-Trigger` 동안 유지
3. 기지국이 UE에게 핸드오버 명령 전송

**이웃 리스트 1위가 아닌 셀로 가는 이유:**
- A3 이벤트를 먼저 충족시킨 셀이 핸드오버 타겟. 가장 강한 셀이 아닐 수 있음
- 오프셋(Offset)과 히스테리시스(Hysteresis) 파라미터가 셀마다 다르게 설정될 수 있음

**목록에 없던 셀로 가는 이유:**
- 앱 수집 주기(5초)보다 핸드오버가 빠름. 핸드오버 직전 수집된 이웃 리스트가 실제 핸드오버 순간과 다를 수 있음
- 이웃 리스트 자체가 modem report 기반으로 주기적으로 업데이트되며 항상 완전하지 않음

---

### [이슈 4] 집에서 상시 로깅 일관성

`NetworkLoggingService`는 `START_STICKY`로 설정되어 있어 시스템이 강제 종료해도 재시작됨.  
단, Android 배터리 최적화(Doze Mode)에 의해 포그라운드 서비스가 제한될 수 있음.

**권장 조치:** 설정 → 앱 → NetworkTrackerApp → 배터리 → "제한 없음" 으로 설정.  
또는 앱 코드에서 `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` Intent로 사용자에게 안내.

---

## 3. 추가 구현 요청

| 기능 | 설명 |
|------|------|
| 핸드오버/핑퐁 감지 | 샘플마다 서빙셀 ID 변화 감지 + 30초 이내 이전 셀 복귀 시 핑퐁 플래그 |
| 가속도 센서 속도 측정 | `TYPE_LINEAR_ACCELERATION` 적분. GPS 속도로 drift 보정 |

---

## 4. 코드 수정 내역

### 수정된 파일

- `app/src/main/java/com/networktracker/data/NetworkRecord.kt`
- `app/src/main/java/com/networktracker/collector/NetworkDataCollector.kt`
- `app/src/main/java/com/networktracker/service/NetworkLoggingService.kt`
- `app/src/main/java/com/networktracker/ui/MainActivity.kt`

---

### NetworkRecord.kt — 신규 필드 추가

```kotlin
val imuSpeedMs: Float? = null,           // 가속도 센서 적분 속도 (m/s) — GPS 불가 시 보조
val handoverDetected: Boolean = false,   // 이전 샘플 대비 서빙셀 ID 변경(핸드오버 발생)
val pingPongDetected: Boolean = false,   // 30초 내 이전 셀로 복귀(핑퐁 핸드오버)
```

CSV 헤더에 `imu_speed_ms`, `handover_detected`, `ping_pong_detected` 컬럼 추가 (neighbors_json 직전).

---

### NetworkDataCollector.kt — 주요 변경사항

#### (1) UNAVAILABLE 필터링 수정 (버그 수정)

```kotlin
// 추가된 확장함수
private fun Int.toJsonOrNull(): Any =
    if (this == Int.MAX_VALUE || this == Int.MIN_VALUE || this == CellInfo.UNAVAILABLE)
        JSONObject.NULL else this

private fun Int.validToString(): String =
    if (this == Int.MAX_VALUE || this == Int.MIN_VALUE || this == CellInfo.UNAVAILABLE)
        "" else this.toString()

// NR NCI (Long 타입) — UNAVAILABLE_LONG = Long.MAX_VALUE
private fun Long.validToString(): String =
    if (this == Long.MAX_VALUE || this == Long.MIN_VALUE) "" else this.toString()
```

이웃셀 JSON의 모든 Int/Long 필드에 `toJsonOrNull()` 적용.  
서빙셀 CI/NCI 저장 시 `validToString()` 적용 → UNAVAILABLE 값 제거.

#### (2) 가속도 센서 기반 속도 추정

```kotlin
private val linearAccelSensor = sensorMgr.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION)

// onSensorChanged: 수평 가속도(ax, ay) 적분 → imuVelocityMs
// 0.3 m/s² 미만이면 정지로 판단, 감쇠(1.5×dt) 적용하여 drift 억제
// GPS 속도가 가용하면 collect() 시점마다 GPS 속도로 리셋 → drift 보정
```

`startImuSensor()` / `stopImuSensor()` 메서드 추가.

#### (3) 핸드오버 / 핑퐁 감지

```kotlin
private val cellHistory = ArrayDeque<Pair<String, Long>>()  // 최근 10회 서빙셀 이력
private var lastCellId = ""

private fun checkHandover(newCellId: String, nowMs: Long): Pair<Boolean, Boolean> {
    // 셀 ID 변화 시 handover = true
    // cellHistory에서 30초 이내 동일 셀 존재 시 pingPong = true
    // 이력 유지: 최대 10건
}
```

`collect()` 반환 직전 `checkHandover(servingCellId, now)` 호출 후 결과를 `NetworkRecord`에 포함.

---

### NetworkLoggingService.kt

```kotlin
// 서비스 시작 시
collector.startImuSensor()

// 서비스 종료 시
collector.stopImuSensor()
```

---

### MainActivity.kt

```kotlin
// onResume / onPause에 IMU 시작·중지 추가
previewCollector.startImuSensor()   // onResume
previewCollector.stopImuSensor()    // onPause

// 라이브 스탯 표시 추가
"IMU속도   : X.X m/s  (X.X km/h)"
"→ 핸드오버 감지"  또는  "⚠ 핑퐁 핸드오버 감지!"
```

---

## 5. CSV 컬럼 변경 요약

| 변경 | 컬럼명 | 설명 |
|------|--------|------|
| 추가 | `imu_speed_ms` | 가속도 센서 적분 속도 (m/s). GPS 가용 시 동기화됨 |
| 추가 | `handover_detected` | true = 이 샘플에서 서빙셀 ID 변경 발생 |
| 추가 | `ping_pong_detected` | true = 30초 이내 이전 셀로 복귀 (핑퐁) |

> 위치: `lte_neighbor_count` 다음, `neighbors_json` 직전

---

## 6. 데이터 분석 시 주의사항

- **이웃셀 `cell_id` / `nci` = null** → 모뎀 미제공. 정상. 셀 구별은 `pci + earfcn` 조합 사용
- **`sinr_snr_db` = 빈칸** → 미측정값. `0`으로 채우지 말고 결측(NaN)으로 처리
- **`handover_detected = true`인 행** → 직전 행과 서빙셀이 다름. 핸드오버 이벤트 분석 기준점으로 사용
- **`ping_pong_detected = true`인 행** → 핸드오버 이후 30초 내 이전 셀 복귀. 핑퐁 판정 기준은 논문 정의에 따라 조정 가능 (현재 30초)
- **`imu_speed_ms`** → GPS 속도 가용 구간에서는 GPS 기준으로 리셋됨. GPS 불가 구간(실내 등)에서는 적분 오차 누적 가능성 있음. GPS 속도와 병행 활용 권장
