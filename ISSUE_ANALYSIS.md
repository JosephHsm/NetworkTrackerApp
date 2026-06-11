# NetworkTrackerApp — 이슈 분석 및 수정 내역

> 논문 데이터 수집 앱을 굴리면서 발견한 문제점들과 코드 수정 기록을 정리한 것이다.
> 최초 작성: 2026-05-17 / 데이터 검증 및 갱신: 2026-06-02

---

## 1. 이슈 목록

| # | 증상 | 분류 | 검증 결과 |
|---|------|------|-----------|
| 1 | 핑퐁 핸드오버 발생 | 분석 + 감지 기능 추가 | 실측 82건 확인 |
| 2 | 가속도 센서 속도 측정 추가 | 기능 추가 | 구현 완료 |
| 3 | 데이터레이트 급등 시 RSRP 변화 없음 | 원인 분석 | 데이터로 검증됨 |
| 4 | 집에서 상시 로깅 일관성 확인 | 운용 이슈 | home 파일로 검증됨 |
| 5 | `sinr_snr_db` 빈칸 자주 발생 | 원인 분석 | 실측 19.8% 결측 확인 |
| 6 | 서빙셀 ID 자릿수 차이 | 버그 + 원인 분석 | 코드 버그 확인, 현재 환경 미발현 |
| 7 | 이웃셀 `cell_id` 전부 동일값 | 버그 | 데이터로 완전 검증됨 |
| 8 | 핸드오버 타겟 셀 선택 규칙 불명 | 원인 분석 | 이론 설명은 되지만 데이터 직접 검증 불가 |
| 9 | 이웃 리스트 전반 검증 | 버그 포함 | 이슈 7과 동일 원인 |
| 10 | 지하 구간 GPS 좌표 동결 | 개선 + 한계 분석 | 부분 개선, 실측 검증됨 → `CHANGES_GPS_IMPROVEMENT.md` |

---

## 2. 이슈별 원인 분석 및 데이터 검증

### [이슈 7 / 이슈 9] 이웃셀 cell_id 전부 동일

근본 원인은 Android `allCellInfo()` API의 설계 한계다.
모뎀은 이웃 셀의 Cell Identity를 거의 안 주고, 읽으면 `CellInfo.UNAVAILABLE = 2147483647`이 반환된다.

데이터 검증 결과 (2026-06-02 실측):

| 버전 | 이웃셀 수 | 2147483647 비율 | null 비율 | 유효 CI 비율 |
|------|----------|----------------|-----------|-------------|
| 구버전 (20260506) | 7,053 | 100.0% | 0% | 0% |
| 신버전 (20260512+) | 30,173 | 40.5% | 59.5% | 0% |

구버전은 주장 그대로 전부 `2147483647`이고, 신버전은 수정 후 null로 바뀌었다.
단, 유효 CI는 0건이다. Android 모뎀이 이웃셀 CI를 실제로는 안 준다는 사실이 확인된다.

결론: 이웃셀 CI는 Android에서 기본적으로 미제공이다. `null`이 정상이다.

이웃셀을 식별할 수 있는지 실측으로 검증해 봤다.

PCI(0–503)는 인접 셀 간 충돌만 막도록 설계돼서 원거리에서 재사용된다.

실측 데이터(서울 지하철 4개 세션) 검증 결과:

| 지표 | 값 |
|------|---|
| PCI+EARFCN 고유 조합 수 | 260개 |
| CI 2개 이상 충돌하는 조합 수 | 104개 (40%) |
| 최악 사례 | PCI=485, EARFCN=100 → 7개 다른 eNB에서 동시 사용 |

이웃셀의 전역 고유 식별은 현재 Android API로는 불가능하다.
PCI+EARFCN은 단일 세션의 로컬 범위(수 km 이내)에서만 임시 구별 수단으로 쓸 수 있고, 세션을 넘나들거나 서울 전역 분석에는 못 쓴다.

---

### [이슈 6] 서빙셀 ID 자릿수 차이 (PCI/EARFCN은 동일)

근본 원인은 LTE와 NR의 Cell Identity 비트 폭 차이다.

| 규격 | 필드 | 비트 | 최대값 | 최대 자릿수 |
|------|------|------|--------|------------|
| LTE  | CI (Cell Identity) | 28 bit | 268,435,455 | 9자리 |
| NR   | NCI (NR Cell Identity) | 36 bit | 68,719,476,735 | 11자리 |

5G NSA 환경에서는 LTE 앵커셀과 NR 셀이 동시에 `isRegistered=true`로 반환될 수 있다. 코드가 `allCellInfo`를 순회하다가 마지막으로 처리한 셀이 `servingCellId`를 overwrite하는데, NR 셀이 마지막이면 서빙셀 ID가 NCI(더 긴 숫자)로 기록된다.

데이터 검증 결과 (2026-06-02 실측):

```
서빙셀 ID 자릿수 분포:
  8자리: 4,888건 (100%)
  최대값: 52,560,931 (26-bit)
```

현재 수집 환경(LG U+ NSA)에서는 NR NCI가 서빙셀로 기록된 사례가 없다. NSA 모드에서 LTE가 항상 앵커(serving cell)라서 이 버그가 발현되지 않았다. NR SA 환경에서는 발현될 수 있다.

추가로 DSS(Dynamic Spectrum Sharing)도 확인했다. 서빙셀과 PCI/EARFCN이 같은 이웃셀 케이스를 500행 검사했는데 0건이었다. 현재 수집 환경에서는 DSS를 안 쓴다.

---

### [이슈 5] `sinr_snr_db` 빈칸

원인은 정상 동작이다. 모뎀이 RS-SNR(LTE) / SS-SINR(NR) 값을 항상 보고하지는 않는다.

데이터 검증 결과 (2026-06-02 실측):

| 파일 | 결측률 |
|------|--------|
| walking (20260512) | 67% |
| car (20260524) | 66% |
| subway (20260512) | 51% |
| subway (20260524~0526) | 13~14% |
| walking (20260527) | 14% |
| home (20260529) | ~0% |

파일별로 0~67%까지 크게 다르다. home(실내 고정)에서 결측이 거의 없고, walking 초기 파일에서 가장 높다. 이동 중 신호 변동이 클 때 모뎀 보고가 불안정해지는 것으로 보인다.
유효 SINR 범위는 −9 ~ 30 dB이고, 값이 0인 행도 46건 있으니 공백(NaN)과 0을 구분해야 한다.

---

### [이슈 3] 데이터레이트 급등 / 수신 세기 변화 없음

데이터 검증 결과 (2026-06-02 실측):

```
Rx 5Mbps 이상 급등 이벤트: 258건
급등 시점 RSRP 변화 중앙값: 0.00 dBm
Rx-RSRP 피어슨 상관계수:   r = 0.055  (사실상 무상관)
```

근본 원인은, TrafficStats가 "수요(demand)" 측정이지 "용량(capacity)" 측정이 아니라는 데 있다.

수집 중에 Wi-Fi를 끈 상태였으니 `getTotalRxBytes()`는 셀룰러 트래픽만 기록한다. 그래서 Wi-Fi 오염 가설은 틀렸다.

진짜 이유는 더 구조적이다. TrafficStats는 해당 5초 창에 기기가 실제로 수신한 바이트를 센다. 앱이 아무것도 안 받고 있으면 RSRP가 −60 dBm이든 −90 dBm이든 Rx = 0이다. 반대로 푸시 알림·앱 업데이트·클라우드 싱크가 우연히 그 5초에 터지면, 신호 품질과 무관하게 Rx가 튄다.

```
RSRP = 망이 "얼마나 좋은가" (용량)
Rx   = 그 5초에 앱이 "얼마나 받았는가" (수요)
→ 둘은 독립적
```

`override_network_type=LTE_CA` 구간의 평균 Rx(0.09 Mbps)가 `NONE`(0.085 Mbps)과 차이가 없는 것도 같은 이유다. LTE_CA가 셀룰러 용량을 높여도, 그 순간 앱이 대역폭을 안 쓰면 Rx는 0에 가깝다.

이론상 원인들은 이 데이터로는 검증이 안 된다.

| 원인 | 이론 근거 | 이 데이터에서 검증 가능 여부 |
|------|-----------|--------------------------|
| CA 활성화 | 3GPP 표준, Primary carrier RSRP 유지하면서 Secondary carrier 추가 | 수요 기반 측정이라 용량 증가 효과가 Rx에 안 나타남 |
| 셀 부하 감소 | 기지국 스케줄러 RB 할당 변화 | Android API로 기지국 부하 접근 불가 — 측정 자체 불가 |
| 빔포밍 전환 | NR SA에서 CSI-RSRP 개선, SS-RSRP 유지 | 수집 데이터 전체가 NSA — `csi_rsrp` 전량 공백. 해당 없음 |

진짜 해결책은 능동 측정(active measurement)이다. RSRP↔처리량 상관관계를 제대로 보려면 iperf3 서버를 띄우고 수집 내내 다운로드를 돌려야 한다. 그때의 Rx가 진짜 망 처리량이다. TrafficStats 기반 수동 측정으로는 신호-처리량 상관분석을 믿기 어렵다.

---

### [이슈 4] 집에서 상시 로깅 일관성

`NetworkLoggingService`는 `START_STICKY`로 설정해서 시스템이 강제 종료해도 재시작된다.

데이터 검증 결과 — home 파일 (20260529, 제자리 15.6분 수집):

```
수집 간격 중앙값:    5.0초  (최대 갭: 6.0초)
핸드오버 횟수:      0회
RSRP 평균:         -80.9 dBm, 표준편차 3.48 dBm
SINR 평균:         27.7 dB  (전체 파일 중 최고)
```

수집 주기가 안정적으로 유지된다. SINR 27.7 dB는 이동 중인 다른 파일(subway 평균 14dB, car 평균 9dB)과 비교하면 실내 고정 환경이라는 게 분명히 드러난다.

권장 설정: 설정 → 앱 → NetworkTrackerApp → 배터리 → "제한 없음".
안 해두면 Doze Mode에서 Foreground Service 실행이 지연될 수 있다.

---

### [이슈 1] 핑퐁 핸드오버

1차 검증 (2026-06-02, 수정 전 코드):

```
앱 감지 (ping_pong_detected=true): 45건
분석 스크립트 기준 (30초 이내 복귀): 82건
```

당시 원인은, 앱의 `cellHistory`가 최근 10개 항목만 유지해서 지하철처럼 핸드오버가 연속 10회 이상 발생하면 이전 셀 이력이 덮어써져 핑퐁이 누락된 것이었다.

수정 내용: `cellHistory`를 개수 제한(10개)에서 30초 시간창 기준으로 바꿨다. 이제 30초가 지난 항목만 제거하니까, 고빈도 핸드오버 구간에서도 30초 이내 복귀는 빠짐없이 잡힌다.

```kotlin
val pingPong = cellHistory.any { (id, ts) -> id == newCellId && (nowMs - ts) < 30_000L }
cellHistory.addFirst(Pair(lastCellId, nowMs))
// 개수 제한 대신 30초 이상 지난 항목 제거 — 지하철 고빈도 HO에서 핑퐁 누락 방지
while (cellHistory.isNotEmpty() && (nowMs - cellHistory.last().second) > 30_000L)
    cellHistory.removeLast()
```

2차 검증 (2026-06-09, 수정 후 코드): 앱 감지값과 오프라인 스크립트(30초 복귀)가 완전히 일치한다.

| 파일 | 앱 `ping_pong_detected` | 스크립트(30초 복귀) |
|------|------------------------|--------------------|
| car (20260609_164800) | 13 | 13 |
| subway (20260609_185650) | 0 | 0 |

누락 문제는 해소됐다. car 파일의 핑퐁 13건은 대부분 동일 eNB(예: 205237)의 인접 섹터 간 왕복(intra-site 핑퐁)으로, 셀 경계에서 차량이 오가며 생긴다.

RSRP와 핑퐁의 관계: 핑퐁 핸드오버는 단순히 서빙셀 RSRP가 급락한다고 생기는 게 아니라, 이웃 셀과 서빙셀 간의 상대적 품질 차이, A3 Offset, Hysteresis, Time-to-Trigger 같은 핸드오버 파라미터 조합으로 발생할 수 있다. 그래서 RSRP가 급격히 떨어지지 않은 상황에서도 이웃 셀이 일정 기준 이상 우세하다고 판단되면 핸드오버가 일어날 수 있고, 이동 속도가 빠른 지하철 환경에서는 이런 조건이 짧은 시간 안에 반복돼서 핑퐁 현상으로 나타날 수 있다. // 용어정리: LTE/5G에서 Event A3는 보통 "이웃 셀의 품질이 현재 서빙셀보다 일정 수준 이상 좋아졌을 때" 단말이 측정 보고를 올리고 네트워크가 핸드오버를 판단하는 조건이다. 겨우 1 dB 좋은 정도로는 바로 안 넘어가게 막는 것.

---

### [이슈 8] 핸드오버 타겟 셀 선택 규칙

원인: UE(단말)가 고르는 게 아니라 기지국(eNB/gNB)이 결정한다.

핸드오버 결정 흐름은 이렇다.
1. UE가 측정 보고(Measurement Report) 전송
2. 기지국이 A3 이벤트 기준으로 핸드오버 명령 결정
   - A3 조건: `이웃셀 RSRP > 서빙셀 RSRP + Offset + Hysteresis` 가 `Time-to-Trigger` 동안 유지
3. 기지국이 UE에게 핸드오버 명령 전송

이웃 1위가 아닌 셀로 가는 이유:
- A3 이벤트를 먼저 충족시킨 셀이 타겟이 된다. Offset/Hysteresis가 셀마다 다르게 설정될 수 있다.

목록에 없던 셀로 가는 이유:
- 앱 수집 주기(5초)보다 핸드오버가 빠르다. 핸드오버 직전 수집된 이웃 리스트와 실제 타겟 셀이 다를 수 있다.
- `allCellInfo`가 최신 상태가 아닐 수 있다 (→ `cell_info_age_ms` 컬럼으로 확인 가능).

검증할 수 있는 것과 없는 것을 정리하면:

| 질문 | 가능 여부 | 근거 |
|------|-----------|------|
| 핸드오버가 발생했는가 | 가능 | `handover_detected`, `prev_serving_cell_id` → `serving_cell_id` 변화 |
| 핸드오버 직전 신호가 얼마였는가 | 가능 | `prev_rsrp_dbm`, `prev_rsrq_db` |
| 핸드오버 직전 가장 강한 이웃셀 RSRP | 가능 | `best_nbr_rsrp_dbm` |
| A3 조건이 충족됐는가 (best_nbr_rsrp > prev_rsrp) | 간접 가능 | 위 두 컬럼 비교 |
| 이웃 1위 셀로 갔는지 여부 | 불가 | 이웃셀 CI 미제공(이슈 7) + PCI+EARFCN도 40% 충돌(이슈 9) |
| 목록에 없던 셀로 간 이유 검증 | 불가 | 동일 이유 |

결론: 핸드오버 발생 자체와 그 시점의 신호 상태 분석은 되지만, "어느 이웃셀로 이동했는가"는 현재 Android API 한계로 근본적으로 확인할 수 없다. 전문 드라이브 테스트 장비 없이는 해결이 안 되는 문제다.

---

## 3. 코드 수정 내역 (전체)

### 수정된 파일

- `app/src/main/java/com/networktracker/data/NetworkRecord.kt`
- `app/src/main/java/com/networktracker/collector/NetworkDataCollector.kt`
- `app/src/main/java/com/networktracker/logger/CsvLogger.kt`
- `app/src/main/java/com/networktracker/service/NetworkLoggingService.kt`
- `app/src/main/java/com/networktracker/ui/MainActivity.kt`
- `app/src/main/res/layout/activity_main.xml`

---

### 버그 수정

#### (1) 이웃셀 UNAVAILABLE 필터링

```kotlin
// 수정 전: UNAVAILABLE 값 그대로 저장
put("cell_id", cell.cellIdentity.ci.toString())   // → "2147483647"

// 수정 후: null로 변환
private fun Int?.toJsonOrNull(): Any =
    if (this == null || this == Int.MAX_VALUE || this == Int.MIN_VALUE || this == CellInfo.UNAVAILABLE)
        JSONObject.NULL else this

put("cell_id", id.ifEmpty { null })  // UNAVAILABLE이면 null
```

#### (2) NR NCI UNAVAILABLE_LONG 필터링

```kotlin
private fun Long.validToString(): String =
    if (this == Long.MAX_VALUE || this == Long.MIN_VALUE) "" else this.toString()
```

#### (3) onCellChangeDetected 중복 수집 방지

5초 timer와 핸드오버 콜백이 동시에 발화하면 거의 같은 타임스탬프의 레코드가 2개 생겼다.

```kotlin
// 수정 후: 직전 수집으로부터 1초 이상 경과 시만 수집
collector.onCellChangeDetected = {
    val now = System.currentTimeMillis()
    if (now - lastCollectMs >= 1_000L) {
        lastCollectMs = now
        collector.collectTrigger = "handover"
        val record = collector.collect()
        ...
    }
}
```

---

### 처리량 측정 개선

기존 `getTotalRxBytes()`는 Wi-Fi + 셀룰러 합산이라 신호-처리량 상관분석이 불가능했다.

```kotlin
// 셀룰러 전용 처리량 추가
val curMobileRx = TrafficStats.getMobileRxBytes()
val mobileRxSpeed = if (curMobileRx >= 0 && prevMobileRxBytes >= 0)
    speedBps(prevMobileRxBytes, curMobileRx, elapsedMs) else 0L
```

Wi-Fi 활성 여부도 같이 기록한다.
```kotlin
private fun isWifiActive(): Boolean {
    val caps = cm.getNetworkCapabilities(cm.activeNetwork ?: return false) ?: return false
    return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
}
```

---

### 이웃셀 개선

```kotlin
// RSRP 내림차순 정렬 후 JSONArray 구성
nbrList.sortByDescending { it.rsrp }
val neighbors = JSONArray().also { arr -> nbrList.forEach { arr.put(it.json) } }

// 최강 이웃셀 요약 컬럼 (JSON 파싱 없이 ML 피처로 사용)
if (nRsrp != null && (bestNbrRsrp == null || nRsrp > bestNbrRsrp!!)) {
    bestNbrRsrp = nRsrp; bestNbrPci = nPci; bestNbrArfcn = nEarfcn
}
```

---

### 핸드오버 정보 강화

핸드오버 직전 셀 정보를 저장해서 A3 이벤트 분석이 가능하게 했다.

```kotlin
private data class HandoverResult(
    val handover: Boolean, val pingPong: Boolean,
    val prevCellId: String,
    val prevRsrp: Int?, val prevRsrq: Int?   // 직전 셀의 신호값
)

// 핸드오버 시 lastHandoverMs 기록 → cell_duration_s 계산에 사용
lastHandoverMs = nowMs
```

---

### 파일 저장 방식 개선 (데이터 유실 방지)

```
수정 전: 세션 종료 시 전체 일괄 저장 → 강제 종료 시 전체 유실
수정 후: 30레코드(약 2.5분)마다 append flush → 최대 2.5분치만 유실
```

```kotlin
companion object {
    private const val FLUSH_EVERY = 30
}

fun log(record: NetworkRecord) {
    buffer.add(record)
    if (buffer.size >= FLUSH_EVERY) flush()
}
```

---

### 신규 기능: Activity 태그 UI

로깅 시작 전에 Spinner에서 활동을 고르면 파일명 접미사와 `activity` 컬럼에 같이 기록된다.

| 선택지 | CSV 기록값 |
|--------|-----------|
| 선택 안 함 | `unknown` |
| 도보 | `walking` |
| 지하철 | `subway` |
| 차량 | `car` |
| 실내/정지 | `home` |
| 기타 | `other` |

---

### 신규 기능: requestCellInfoUpdate()

매 tick 후 모뎀에 셀 정보 갱신을 요청한다. 비동기 호출이라 응답이 캐시에 반영되면 다음 tick에서 더 신선한 `allCellInfo`를 읽게 된다.

```kotlin
fun refreshCellInfo() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && hasLocation()) {
        tel.requestCellInfoUpdate(context.mainExecutor, ...)
    }
}
// NetworkLoggingService tick에서 collect() 후 호출
```

---

## 4. 신규 CSV 컬럼 전체 목록

현재 포맷은 61개 컬럼이다. 컬럼 정의의 기준은 `README.md` §데이터 필드 설명이고, 아래는 이번 수정에서 추가한 18개 컬럼이다.

> `location_source` / `location_age_s` 컬럼은 GPS 개선 코드(2026-06-07)에 들어가 있지만, 현재 공개한 측정 데이터셋(5월 + 6월)은 모두 그 이전 빌드로 수집해서 61컬럼이고 해당 컬럼을 포함하지 않는다. 지하 구간 위치 동결은 `gps_speed_ms` 공백 여부로 식별한다(상세: `CHANGES_GPS_IMPROVEMENT.md`).

| 컬럼명 | 분류 | 설명 |
|--------|------|------|
| `activity` | 레이블 | 수집 활동 태그 (ML 지도학습 레이블) |
| `mobile_rx_speed_Bps` | 처리량 | 셀룰러 전용 수신 처리량 |
| `mobile_tx_speed_Bps` | 처리량 | 셀룰러 전용 송신 처리량 |
| `mobile_rx_bitrate_Mbps` | 처리량 | 셀룰러 Rx Mbps — 신호↔처리량 분석에 사용 |
| `mobile_tx_bitrate_Mbps` | 처리량 | 셀룰러 Tx Mbps |
| `wifi_active` | 처리량 | Wi-Fi 연결 여부 (total rx/tx 오염 판단) |
| `best_nbr_rsrp_dbm` | 이웃셀 | 최강 이웃셀 RSRP — JSON 파싱 없이 ML 피처로 사용 |
| `best_nbr_pci` | 이웃셀 | 최강 이웃셀 PCI |
| `best_nbr_arfcn` | 이웃셀 | 최강 이웃셀 EARFCN/NR-ARFCN |
| `collect_trigger` | 수집 컨텍스트 | `periodic`(5초) / `handover`(이벤트) |
| `cell_duration_s` | HO 분석 | 현재 셀에 머문 시간 (초) — HO timing 예측 피처 |
| `ho_count_30s` | HO 분석 | 최근 30초 핸드오버 횟수 — 이동성 지표 |
| `rsrp_delta` | 추세 피처 | RSRP 변화량 (이전 샘플 대비, HO 직후 null) |
| `sinr_delta` | 추세 피처 | SINR 변화량 (이전 샘플 대비, HO 직후 null) |
| `cell_info_age_ms` | 데이터 품질 | allCellInfo 데이터 나이 (ms). 8000+ 이면 stale |
| `prev_serving_cell_id` | HO 분석 | 핸드오버 직전 서빙셀 ID |
| `prev_rsrp_dbm` | HO 분석 | 핸드오버 직전 RSRP — A3 이벤트 분석용 |
| `prev_rsrq_db` | HO 분석 | 핸드오버 직전 RSRQ |

---

## 5. 데이터 분석 시 주의사항 (실측 기반)

- `mobile_rx_bitrate_Mbps`를 쓰자. `rx_bitrate_Mbps`는 Wi-Fi 포함이라, 신호-처리량 분석에는 셀룰러 전용 컬럼을 써야 한다.
- `sinr_snr_db` 결측은 0으로 채우지 말고 NaN으로 둔다. 값 0은 유효한 측정값(0dB)이다.
- `collect_trigger = "handover"` 행은 timer 기반 행과 따로 분석하거나, 이벤트 주도 행을 HO 이벤트 레이블로 쓸 수 있다.
- `cell_info_age_ms > 8000ms` 행은 allCellInfo가 stale일 수 있다. 이웃셀 분석 시 필터링을 권장한다.
- `rsrp_delta` / `sinr_delta`는 핸드오버 직후 행이 null이다(셀이 달라지니 비교가 의미 없음). null을 0으로 채우지 말 것.
- 이웃셀 구별: `cell_id`/`nci`는 항상 null이다. `pci + earfcn` 조합이 유일한 옵션인데, 이건 단일 세션 로컬 범위에서만 유효하다. 실측 결과 PCI+EARFCN 조합의 40%가 서로 다른 eNB에서 충돌했다. 세션을 넘나드는 이웃셀 추적은 Android API 한계로 불가능하다.
- `ping_pong_detected`: 과거 `cellHistory`가 10건만 유지해 고빈도 구간에서 과소 계수되던 문제는 30초 시간창 기준으로 수정·검증을 끝냈다(이슈 1 참조). 2026-06-07 이후 수집분은 앱 값을 그대로 믿어도 된다. 단 30초를 넘겨 복귀하는 케이스는 설계상 핑퐁이 아니므로(정상 재접속) 분석 시 구분할 것.
- 구버전 파일 (20260506): `activity`, `mobile_rx_*`, `wifi_active`, `handover_detected` 등 신규 컬럼이 없다. 혼합 학습 시 컬럼 불일치에 주의할 것.

---

## 6. ARFCN과 PCI에 대한 정리 (공부 메모)

핸드오버 분석을 공부하면서 헷갈렸던 부분인데, 다른 AI한테 물어보고 정리한 내용이다.

ARFCN은 셀 ID가 아니라 주파수 채널 번호다. LTE는 EARFCN, 5G NR은 NR-ARFCN이라고 부르며, 해당 셀이 어떤 주파수를 쓰는지를 나타낸다. 서로 다른 기지국이 같은 주파수 채널(같은 EARFCN)을 얼마든지 공유할 수 있다. 그래서 EARFCN이 같다고 같은 셀이 아니고, PCI+EARFCN 조합도 전역 고유 ID가 아니다. 앞서 실측에서 PCI+EARFCN 조합 40%가 충돌한 이유가 바로 이것이다.

PCI+EARFCN 조합이 갖는 의미는 고유 식별이 아니라 핸드오버 유형 구분에 있다.

| 변화 패턴 | 의미 |
|-----------|------|
| PCI 변화 + EARFCN 동일 | 같은 주파수 대역 안에서 다른 셀로 이동 (intra-frequency HO) |
| PCI 동일 + EARFCN 변화 | 주파수 채널이 바뀜 — LTE 밴드 변경 또는 캐리어 변경 가능성 |
| PCI 변화 + EARFCN 변화 | 밴드도 셀도 모두 바뀜 (inter-frequency HO) |

정확한 셀 식별을 하려면 PCI, EARFCN 외에 CI(Cell Identity), TAC, NCI를 함께 봐야 한다. 그런데 이웃셀의 CI는 Android API로 못 받아오니(이슈 7), 이웃셀에 대해서는 핸드오버 유형 정도만 추정할 수 있고 "어느 특정 셀인가"는 여전히 알 수 없다.

5G NR-ARFCN은 3GPP TS 38.104/38.101 규격에 정의된 방식으로 실제 중심 주파수를 계산할 수 있다. 이 앱에서 수집하는 `serving_freq_arfcn` 값으로 접속 중인 주파수를 역산할 수 있다.

---

## 7. 향후 개선 제안 (메모)

신호-처리량 상관관계를 제대로 보려면 iperf3 같은 능동 측정 방식이 필요하다. 클라우드 서버를 열어두고 수집 내내 다운로드를 강제로 돌리는 방식인데, 그러면 TrafficStats의 수동 측정 문제가 해결되고 RSRP와 실제 가용 처리량의 관계를 볼 수 있다. 다만 서버 구축·운용 비용이 들고, 1시간 지하철 수집 한 번에 데이터를 수십 GB 소모하는 문제도 있어서 학기 중에 당장 적용하기는 어렵다. 나중에 여유가 생기면 시도해 볼 것.
