package com.networktracker.collector

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.URL
import java.util.ArrayDeque
import java.util.Locale
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * 능동 측정(active measurement) 프로브.
 *
 * TrafficStats 기반 수동 측정은 "수요"라서 망 "용량"을 못 본다(ISSUE_ANALYSIS.md 이슈 3).
 * 그래서 앱이 직접 부하를 만들어 측정한다.
 *
 *  1. RTT: 매 tick마다 TCP connect 소요시간 측정 (~수백 바이트).
 *     핸드오버 순간의 지연 스파이크(제어평면 중단시간)가 그대로 드러난다.
 *     망·사업자에 따라 특정 IP/포트가 막힐 수 있어 RTT_TARGETS를 차례로 시도하고, 처음 성공한 대상을 계속 쓴다.
 *  2. 지속 부하: 초당 STEADY_BPS 바이트를 끊지 않고 흘린다 (다운로드·업로드 각각).
 *
 * 부하를 "버스트"가 아니라 "지속"으로 거는 이유 ─────────────────────────────
 * 예전에는 30초마다 2MB를 몰아서 받았다. 데이터 총량은 같지만(2MB/30초 = 60,000 B/s),
 * 부하가 걸린 몇 초 말고는 rx/tx가 0이라 대부분의 행이 "아무것도 주고받지 않던 순간"으로 남았다
 * (9월 측정 기준 트래픽이 있던 행은 33%뿐). 그래서 신호↔처리량 관계를 볼 표본이 늘 모자랐다.
 * 같은 양을 초당 60,000 B로 고르게 펴면 모든 행에 처리량이 찍힌다.
 *
 * 페이싱: 목표보다 앞서면 그만큼 쉰다. 그래서 망이 멀쩡하면 probe_dl/ul_mbps는 목표치
 * (0.48 Mbps) 근처에 머물고, **망이 못 따라오는 구간에서만 목표 아래로 떨어진다.**
 * 즉 이 값이 목표에 못 미치는 구간이 곧 열악한 구간이다.
 *
 * 두 측정 모두 셀룰러 네트워크에 명시적으로 바인딩한다 — Wi-Fi가 켜져 있어도
 * 셀룰러 경로를 측정하므로 wifi_active 오염 문제가 없다.
 * requestNetwork가 실패하면(권한·단말 정책) 현재 기본 망이 셀룰러일 때만 그 망으로 대신 측정한다.
 *
 * 실패는 조용히 삼키지 않고 rttStatus / dlStatus / ulStatus에 이유를 남긴다 — 화면과 알림에 그대로 표시된다.
 * (v1.1 초기 빌드는 CHANGE_NETWORK_STATE 권한 누락으로 requestNetwork가 SecurityException을 던졌는데,
 *  runCatching이 이를 삼켜서 rtt_ms / probe_dl_mbps가 한 번도 기록되지 않았다.)
 *
 * 데이터 소모: 다운·업 각각 시간당 약 216MB (둘 다 켜면 432MB).
 * SESSION_BYTE_CAP 도달 시 부하만 자동 중지되고 RTT와 수집은 계속된다.
 *
 * 3. 속도 측정(Speed Test, v1.3): speedTestIntervalMs마다 다운로드를 페이싱 없이 SPEEDTEST_MS 동안 최대한 받는다.
 *    지속 부하는 목표(0.48 Mbps)에 묶여 있어 망 용량을 못 보고(망이 멀쩡하면 늘 목표치),
 *    TrafficStats 수신 속도는 폰이 받는 양에 좌우돼 신호와의 관계를 가를 수 없었다(2026-10 리포트 그림 6·15).
 *    그래서 "이 순간 이 셀에서 받을 수 있는 최대 속도"를 직접 잰다.
 *    - 첫 바이트 뒤 SPEEDTEST_WARMUP_MS는 TCP 느린 시작이라 속도 계산에서 뺀다.
 *    - 시작 직전과 끝난 직후에 서비스가 행을 하나씩 더 기록한다(collect_trigger = speedtest_start / speedtest).
 *      그래서 같은 몇 초 구간의 RSRP·SINR과 실제 다운로드 속도를 한 쌍으로 비교할 수 있다.
 *    - 데이터 소모는 망 속도에 비례한다: 50 Mbps 망에서 1회 약 19MB, 60초 간격이면 시간당 약 1.1GB.
 *      SPEEDTEST_BYTE_CAP(세션당)에 닿으면 속도 측정만 멈춘다.
 */
class ActiveProbe(context: Context) {

    companion object {
        private const val TAG = "ActiveProbe"
        private val RTT_TARGETS = listOf("8.8.8.8" to 53, "1.1.1.1" to 443, "speed.cloudflare.com" to 443)
        private const val RTT_TIMEOUT_MS = 2_000

        /** 초당 목표 바이트 — 0.06 MB/s. 예전 버스트(2MB/30초)와 같은 양을 고르게 편 것. */
        const val STEADY_BPS = 60_000L
        /** 한 연결에서 주고받을 양. 다 쓰면 새 연결을 연다 (6MB ≒ 100초). */
        private const val CHUNK_BYTES = 6_000_000L
        private const val DL_URL = "https://speed.cloudflare.com/__down?bytes=$CHUNK_BYTES"
        private const val UL_URL = "https://speed.cloudflare.com/__up"
        private const val IO_TIMEOUT_MS = 15_000
        private const val IO_BUF = 8 * 1024
        /** 실측 속도를 내는 창 — 이 구간 동안 실제로 오간 바이트로 계산한다. */
        private const val RATE_WINDOW_MS = 1_000L
        const val SESSION_BYTE_CAP = 1_000L * 1024 * 1024   // 1 GB (다운+업 합산)

        /** 속도 측정 한 번의 길이 (첫 바이트부터). */
        private const val SPEEDTEST_MS = 3_000L
        /** 이 구간은 TCP 느린 시작이라 속도 계산에서 뺀다. */
        private const val SPEEDTEST_WARMUP_MS = 500L
        /** 한 번에 요청하는 양 — 3초 안에 다 받으면(≥130 Mbps) 거기서 끝난다. */
        private const val SPEEDTEST_REQ_BYTES = 50_000_000L
        private const val SPEEDTEST_URL = "https://speed.cloudflare.com/__down?bytes=$SPEEDTEST_REQ_BYTES"
        const val SPEEDTEST_BYTE_CAP = 2_000L * 1024 * 1024  // 2 GB — 지속 부하 한도와 따로 센다
        /** 핸드오버 측정은 직전 측정이 끝나고 이만큼 지나야 한다 — 핑퐁 때 연달아 받지 않도록. */
        private const val HANDOVER_MIN_GAP_MS = 10_000L
    }

    /** 속도 측정 한 번의 결과. 실패하면 mbps가 null이고 error에 이유가 남는다. */
    data class SpeedTestResult(
        val mbps: Double?,          // 워밍업 뒤 구간의 평균 다운로드 속도
        val bytes: Long,            // 받은 전체 바이트 (워밍업 포함)
        val durationMs: Long,       // 첫 바이트부터 끝까지
        val ttfbMs: Long?,          // 요청 ~ 첫 바이트
        val error: String? = null,
        val reason: String = "interval"   // "interval"(정기) | "handover"(셀이 바뀐 직후)
    )

    private val cm = context.applicationContext
        .getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    @Volatile private var cellularNetwork: Network? = null
    @Volatile private var requestError: String? = null
    @Volatile private var running = false
    @Volatile private var dlEnabled = false
    @Volatile private var ulEnabled = false
    @Volatile var probeBytesUsed = 0L; private set

    /** 마지막 RTT (ms). 실패/타임아웃 시 null. collect()가 매번 읽는다. */
    @Volatile var lastRttMs: Int? = null; private set

    /** 사람이 읽는 상태. "정상 …" 또는 "실패 — 이유". */
    @Volatile var rttStatus = "시작 전"; private set
    @Volatile var dlStatus = "꺼짐"; private set
    @Volatile var ulStatus = "꺼짐"; private set
    @Volatile var stStatus = "꺼짐"; private set
    @Volatile var speedTestBytesUsed = 0L; private set
    @Volatile var lastSpeedTest: SpeedTestResult? = null; private set

    /** 속도 측정 직전·직후에 불린다(측정 스레드). 서비스가 이때 행을 하나씩 더 기록한다. */
    @Volatile var onSpeedTestStart: (() -> Unit)? = null
    @Volatile var onSpeedTestDone: ((SpeedTestResult) -> Unit)? = null
    @Volatile private var stThread: Thread? = null
    private val stRequests = LinkedBlockingQueue<String>()
    private var stCount = 0; private var stFail = 0

    private var rttTargetIdx = 0
    private var rttOk = 0; private var rttFail = 0
    private var dlFail = 0; private var ulFail = 0

    private val dlRate = RateWindow()
    private val ulRate = RateWindow()

    /** 최근 1초 실제 수신/송신 속도 (Mbps). 부하가 꺼져 있으면 null. */
    val lastDlMbps: Double? get() = if (dlEnabled) dlRate.mbps() else null
    val lastUlMbps: Double? get() = if (ulEnabled) ulRate.mbps() else null

    // RTT와 부하는 서로 다른 스레드에서 돈다. 한 스레드를 공유하면 부하 루프가 RTT tick을 막아,
    // 하필 "부하가 걸린 순간"의 지연이 측정에서 빠진다. 다운로드와 업로드도 서로 막지 않게 나눈다.
    private var rttThread: HandlerThread? = null
    private var rttHandler: Handler? = null
    private var dlThread: Thread? = null
    private var ulThread: Thread? = null
    private var intervalMs = 5_000L

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) { cellularNetwork = network }
        override fun onLost(network: Network) {
            if (cellularNetwork == network) cellularNetwork = null
        }
    }

    private val rttTick = object : Runnable {
        override fun run() {
            if (!running) return
            measureRtt()
            rttHandler?.postDelayed(this, intervalMs)
        }
    }

    /** @param speedTestIntervalMs 0이면 속도 측정을 하지 않는다 */
    fun start(collectIntervalMs: Long, downloadEnabled: Boolean, uploadEnabled: Boolean, speedTestIntervalMs: Long = 0L) {
        if (running) return
        running = true
        dlEnabled = downloadEnabled
        ulEnabled = uploadEnabled
        intervalMs = collectIntervalMs
        probeBytesUsed = 0L
        lastRttMs = null
        requestError = null
        rttOk = 0; rttFail = 0; dlFail = 0; ulFail = 0
        dlRate.reset(); ulRate.reset()
        speedTestBytesUsed = 0L; lastSpeedTest = null; stCount = 0; stFail = 0
        stStatus = if (speedTestIntervalMs > 0) "첫 측정 대기 중" else "꺼짐 (스위치 OFF)"
        rttStatus = "셀룰러 망 요청 중"
        dlStatus = if (downloadEnabled) "연결 중" else "꺼짐 (스위치 OFF)"
        ulStatus = if (uploadEnabled) "연결 중" else "꺼짐 (스위치 OFF)"

        try {
            cm.requestNetwork(
                NetworkRequest.Builder()
                    .addTransportType(NetworkCapabilities.TRANSPORT_CELLULAR)
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    .build(),
                networkCallback
            )
        } catch (e: Exception) {
            requestError = "${e.javaClass.simpleName}: ${e.message}"
            Log.e(TAG, "셀룰러 망 요청 실패", e)
        }

        rttThread = HandlerThread("ActiveProbe-RTT").also { it.start() }
        rttHandler = Handler(rttThread!!.looper).also { it.post(rttTick) }

        if (downloadEnabled) {
            dlThread = Thread({ steadyLoop(down = true) }, "ActiveProbe-DL").also { it.start() }
        }
        if (uploadEnabled) {
            ulThread = Thread({ steadyLoop(down = false) }, "ActiveProbe-UL").also { it.start() }
        }
        if (speedTestIntervalMs > 0) {
            stThread = Thread({ speedTestLoop(speedTestIntervalMs) }, "ActiveProbe-ST").also { it.start() }
        }
    }

    fun stop() {
        running = false
        runCatching { cm.unregisterNetworkCallback(networkCallback) }
        cellularNetwork = null
        rttHandler?.removeCallbacksAndMessages(null)
        rttThread?.quitSafely()
        rttThread = null; rttHandler = null
        // 부하 루프는 running=false를 보고 스스로 빠져나온다. 소켓 읽기/쓰기에 걸려 있을 수 있어
        // 인터럽트로 깨우되, 타임아웃이 있으므로 끝을 기다리지는 않는다.
        dlThread?.interrupt(); ulThread?.interrupt(); stThread?.interrupt()
        dlThread = null; ulThread = null; stThread = null
    }

    /** 화면 표시용 세 줄 요약. */
    fun statusText(): String = "RTT: $rttStatus\nDL : $dlStatus\nUL : $ulStatus\nST : $stStatus"

    // ── 측정 구현 ─────────────────────────────────────────────────────────────

    /** requestNetwork로 받은 셀룰러 망. 못 받았으면 현재 기본 망이 셀룰러일 때 그 망을 쓴다. */
    private fun cellular(): Network? {
        cellularNetwork?.let { return it }
        val active = cm.activeNetwork ?: return null
        val caps = cm.getNetworkCapabilities(active) ?: return null
        return if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) active else null
    }

    private fun noNetworkReason(): String =
        requestError?.let { "셀룰러 망 요청 오류 ($it)" } ?: "셀룰러 망 없음 (모바일 데이터가 켜져 있는지 확인)"

    private fun measureRtt() {
        val net = cellular()
        if (net == null) {
            lastRttMs = null
            rttStatus = "실패 — ${noNetworkReason()}"
            return
        }
        for (k in RTT_TARGETS.indices) {
            val i = (rttTargetIdx + k) % RTT_TARGETS.size
            val (host, port) = RTT_TARGETS[i]
            val ms = runCatching {
                val addr = net.getByName(host)   // DNS 조회는 지연 시간에서 뺀다
                net.socketFactory.createSocket().use { sock ->
                    val t0 = System.nanoTime()
                    sock.connect(InetSocketAddress(addr, port), RTT_TIMEOUT_MS)
                    ((System.nanoTime() - t0) / 1_000_000L).toInt()
                }
            }.onFailure { Log.w(TAG, "RTT $host:$port 실패: $it") }.getOrNull()
            if (ms != null) {
                rttTargetIdx = i
                rttOk++
                lastRttMs = ms
                rttStatus = "정상 $ms ms ($host:$port)"
                return
            }
        }
        rttFail++
        lastRttMs = null
        rttStatus = "실패 — 모든 대상 응답 없음 (성공 $rttOk / 실패 $rttFail)"
    }

    /**
     * 지속 부하 루프. 연결이 끊기거나 한 덩어리를 다 쓰면 새로 연결해 계속 흘린다.
     * 세션 한도에 닿으면 흘리기만 멈춘다 — RTT와 수집은 그대로 이어진다.
     */
    private fun steadyLoop(down: Boolean) {
        while (running) {
            if (probeBytesUsed >= SESSION_BYTE_CAP) {
                val msg = "중지 — 세션 한도 ${SESSION_BYTE_CAP / 1_048_576}MB 도달"
                if (down) dlStatus = msg else ulStatus = msg
                if (!sleepMs(1_000L)) return
                continue
            }
            val net = cellular()
            if (net == null) {
                val msg = "실패 — ${noNetworkReason()}"
                if (down) dlStatus = msg else ulStatus = msg
                if (!sleepMs(1_000L)) return
                continue
            }
            runCatching { if (down) pumpDown(net) else pumpUp(net) }
                .onFailure { e ->
                    if (!running) return
                    if (down) dlFail++ else ulFail++
                    val msg = "재연결 — ${e.javaClass.simpleName}: ${e.message} (끊김 ${if (down) dlFail else ulFail}회)"
                    if (down) dlStatus = msg else ulStatus = msg
                    Log.w(TAG, "지속 ${if (down) "다운로드" else "업로드"} 끊김", e)
                    sleepMs(500L)
                }
        }
    }

    private fun pumpDown(net: Network) {
        val conn = net.openConnection(URL(DL_URL)) as HttpURLConnection
        conn.connectTimeout = IO_TIMEOUT_MS
        conn.readTimeout = IO_TIMEOUT_MS
        conn.useCaches = false
        try {
            val code = conn.responseCode
            if (code != HttpURLConnection.HTTP_OK) {
                dlFail++
                dlStatus = "실패 — HTTP $code (끊김 ${dlFail}회)"
                sleepMs(1_000L)
                return
            }
            val buf = ByteArray(IO_BUF)
            var moved = 0L
            val t0 = System.nanoTime()
            conn.inputStream.use { input ->
                while (running && probeBytesUsed < SESSION_BYTE_CAP) {
                    val n = input.read(buf)
                    if (n < 0) break
                    moved += n
                    probeBytesUsed += n
                    dlRate.add(n.toLong())
                    dlStatus = rateText(dlRate, dlFail)
                    if (!pace(moved, t0)) return
                }
            }
        } finally {
            conn.disconnect()
        }
    }

    private fun pumpUp(net: Network) {
        val conn = net.openConnection(URL(UL_URL)) as HttpURLConnection
        conn.connectTimeout = IO_TIMEOUT_MS
        conn.readTimeout = IO_TIMEOUT_MS
        conn.useCaches = false
        conn.doOutput = true
        conn.requestMethod = "POST"
        // 전체 길이를 미리 알리지 않고 흘려보낸다 — 메모리에 쌓이지 않고 바로 망으로 나간다.
        conn.setChunkedStreamingMode(IO_BUF)
        try {
            val buf = ByteArray(IO_BUF)          // 내용은 의미 없다. 0으로 채워 보낸다.
            var moved = 0L
            val t0 = System.nanoTime()
            conn.outputStream.use { out ->
                while (running && moved < CHUNK_BYTES && probeBytesUsed < SESSION_BYTE_CAP) {
                    out.write(buf)
                    out.flush()
                    moved += buf.size
                    probeBytesUsed += buf.size
                    ulRate.add(buf.size.toLong())
                    ulStatus = rateText(ulRate, ulFail)
                    if (!pace(moved, t0)) return
                }
            }
            val code = conn.responseCode          // 응답까지 읽어야 연결이 깔끔하게 닫힌다
            if (code != HttpURLConnection.HTTP_OK) {
                ulFail++
                ulStatus = "실패 — HTTP $code (끊김 ${ulFail}회)"
                sleepMs(1_000L)
            }
        } finally {
            conn.disconnect()
        }
    }

    /**
     * 핸드오버가 감지되면 서비스가 부른다 — 새 셀에서 바로 속도를 잰다.
     * 직전 측정이 끝난 지 HANDOVER_MIN_GAP_MS가 안 됐으면 건너뛴다(핑퐁 때 연달아 받지 않도록).
     */
    fun requestSpeedTest(reason: String) {
        if (stThread == null) return
        stRequests.offer(reason)
    }

    /**
     * 속도 측정 루프 — 세션 시작 10초 뒤 첫 측정, 이후 intervalMs마다, 그리고 핸드오버 때마다.
     * 어느 쪽으로 쟀든 다음 정기 측정은 그 측정 시점부터 intervalMs 뒤다.
     */
    private fun speedTestLoop(intervalMs: Long) {
        stRequests.clear()
        if (!sleepMs(10_000L)) return
        var reason = "interval"
        var lastEnd = 0L
        while (running) {
            val t0 = System.currentTimeMillis()
            if (speedTestBytesUsed >= SPEEDTEST_BYTE_CAP) {
                stStatus = "중지 — 세션 한도 ${SPEEDTEST_BYTE_CAP / 1_048_576}MB 도달"
                return
            }
            val net = cellular()
            val result = (if (net == null) {
                SpeedTestResult(null, 0, 0, null, noNetworkReason())
            } else {
                onSpeedTestStart?.invoke()
                // 시작 행이 먼저 기록되도록 잠깐 기다린다 (수집은 메인 스레드에서 돈다)
                if (!sleepMs(300L)) return
                runCatching { runSpeedTest(net) }
                    .getOrElse { e -> SpeedTestResult(null, 0, 0, null, "${e.javaClass.simpleName}: ${e.message}") }
            }).copy(reason = reason)
            if (!running) return
            lastSpeedTest = result
            lastEnd = System.currentTimeMillis()
            if (result.mbps != null) stCount++ else stFail++
            stStatus = result.mbps?.let {
                String.format(Locale.US, "정상 %.1f Mbps [%s] (%.1fMB/%.1f초, %d회·실패 %d회, %dMB 사용)",
                    it, if (reason == "handover") "핸드오버" else "정기", result.bytes / 1e6, result.durationMs / 1000.0,
                    stCount, stFail, speedTestBytesUsed / 1_048_576)
            } ?: "실패 — ${result.error} (성공 $stCount / 실패 $stFail)"
            // 망이 없어 시작 행을 안 남겼으면 끝 행도 남기지 않는다
            if (net != null) onSpeedTestDone?.invoke(result)

            // 다음 측정까지 기다린다. 기다리는 동안 핸드오버 요청이 오면 바로 깬다.
            // 측정 중에 쌓인 요청(측정 도중의 핸드오버)은 버린다 — 그 셀은 이미 방금 쟀다.
            stRequests.clear()
            reason = "interval"
            val due = t0 + intervalMs
            while (running) {
                val left = due - System.currentTimeMillis()
                if (left <= 0) break
                val req = try { stRequests.poll(left, TimeUnit.MILLISECONDS) } catch (e: InterruptedException) { return }
                if (req != null && System.currentTimeMillis() - lastEnd >= HANDOVER_MIN_GAP_MS) { reason = req; break }
            }
        }
    }

    /** 페이싱 없이 SPEEDTEST_MS 동안 받을 수 있는 만큼 받는다. */
    private fun runSpeedTest(net: Network): SpeedTestResult {
        stStatus = "측정 중..."
        val conn = net.openConnection(URL(SPEEDTEST_URL)) as HttpURLConnection
        conn.connectTimeout = IO_TIMEOUT_MS
        conn.readTimeout = IO_TIMEOUT_MS
        conn.useCaches = false
        val tReq = System.nanoTime()
        try {
            val code = conn.responseCode
            if (code != HttpURLConnection.HTTP_OK) return SpeedTestResult(null, 0, 0, null, "HTTP $code")
            val buf = ByteArray(64 * 1024)
            var total = 0L
            var afterWarm = 0L
            var tFirst = 0L
            var tWarm = 0L
            var tLast = 0L
            conn.inputStream.use { input ->
                while (running) {
                    val n = input.read(buf)
                    if (n < 0) break
                    val now = System.nanoTime()
                    if (tFirst == 0L) { tFirst = now; tWarm = now + SPEEDTEST_WARMUP_MS * 1_000_000L }
                    total += n
                    speedTestBytesUsed += n
                    if (now >= tWarm) afterWarm += n
                    tLast = now
                    if ((now - tFirst) / 1_000_000L >= SPEEDTEST_MS) break
                }
            }
            if (tFirst == 0L) return SpeedTestResult(null, 0, 0, null, "받은 데이터 없음")
            val durMs = (tLast - tFirst) / 1_000_000L
            val warmMs = (tLast - tWarm) / 1_000_000L
            // 워밍업 전에 다 받아버린 아주 빠른 경우는 전체 구간으로 계산한다
            val mbps = when {
                warmMs >= 200 && afterWarm > 0 -> afterWarm * 8.0 / 1_000.0 / warmMs
                durMs > 0 -> total * 8.0 / 1_000.0 / durMs
                else -> null
            }
            return SpeedTestResult(mbps, total, durMs, (tFirst - tReq) / 1_000_000L,
                if (mbps == null) "측정 구간이 너무 짧음" else null)
        } finally {
            conn.disconnect()
        }
    }

    /**
     * 목표 페이스(STEADY_BPS)보다 앞서 있으면 그만큼 쉰다.
     * 뒤처져 있으면 쉬지 않는다 — 망이 느린 만큼 그대로 느리게 기록된다.
     * @return 계속 진행할지 여부 (false면 중단 요청)
     */
    private fun pace(movedBytes: Long, startNs: Long): Boolean {
        val dueMs = movedBytes * 1_000L / STEADY_BPS
        val elapsedMs = (System.nanoTime() - startNs) / 1_000_000L
        val waitMs = dueMs - elapsedMs
        return if (waitMs > 0) sleepMs(waitMs) else running
    }

    /** @return 끝까지 잤으면 true, 중단/인터럽트면 false */
    private fun sleepMs(ms: Long): Boolean {
        if (!running) return false
        return try {
            Thread.sleep(ms)
            running
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            false
        }
    }

    private fun rateText(w: RateWindow, fails: Int): String {
        val mbps = w.mbps()
        val target = STEADY_BPS * 8.0 / 1_000_000.0
        val lag = if (mbps != null && mbps < target * 0.8) " ← 목표 미달" else ""
        return String.format(
            Locale.US, "정상 %s / 목표 %.2f Mbps%s (끊김 %d회, %dMB 사용)",
            mbps?.let { String.format(Locale.US, "%.2f", it) } ?: "-", target, lag,
            fails, probeBytesUsed / 1_048_576
        )
    }

    /** 최근 RATE_WINDOW_MS 동안 오간 바이트로 실측 속도를 낸다. */
    private class RateWindow {
        private val times = ArrayDeque<Long>()
        private val sizes = ArrayDeque<Long>()
        private var sum = 0L

        @Synchronized fun reset() { times.clear(); sizes.clear(); sum = 0L }

        @Synchronized fun add(bytes: Long) {
            val now = System.currentTimeMillis()
            times.addLast(now); sizes.addLast(bytes)
            sum += bytes
            trim(now)
        }

        @Synchronized fun mbps(): Double {
            trim(System.currentTimeMillis())
            return sum * 8.0 / 1_000_000.0 / (RATE_WINDOW_MS / 1_000.0)
        }

        private fun trim(now: Long) {
            while (times.isNotEmpty() && now - times.peekFirst() > RATE_WINDOW_MS) {
                times.removeFirst()
                sum -= sizes.removeFirst()
            }
        }
    }
}
