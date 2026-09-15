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
import java.util.Locale

/**
 * 능동 측정(active measurement) 프로브.
 *
 * TrafficStats 기반 수동 측정은 "수요"라서 망 "용량"을 못 본다(ISSUE_ANALYSIS.md 이슈 3).
 * 유튜브 스트리밍도 ABR이 버퍼를 채우면 다운로드를 멈춰서 부하가 불연속적이다.
 * 그래서 앱이 직접 부하를 만들어 측정한다.
 *
 *  1. RTT: 매 tick마다 TCP connect 소요시간 측정 (~수백 바이트).
 *     핸드오버 순간의 지연 스파이크(제어평면 중단시간)가 그대로 드러난다.
 *     망·사업자에 따라 특정 IP/포트가 막힐 수 있어 RTT_TARGETS를 차례로 시도하고, 처음 성공한 대상을 계속 쓴다.
 *  2. 다운로드 버스트: DL_INTERVAL_MS마다 고정 크기 HTTP 다운로드로 순간 처리량 측정.
 *     Cloudflare 공개 엔드포인트를 써서 별도 서버가 필요 없다.
 *
 * 두 측정 모두 셀룰러 네트워크에 명시적으로 바인딩한다 — Wi-Fi가 켜져 있어도
 * 셀룰러 경로를 측정하므로 wifi_active 오염 문제가 없다.
 * requestNetwork가 실패하면(권한·단말 정책) 현재 기본 망이 셀룰러일 때만 그 망으로 대신 측정한다.
 *
 * 실패는 조용히 삼키지 않고 rttStatus / dlStatus에 이유를 남긴다 — 화면과 알림에 그대로 표시된다.
 * (v1.1 초기 빌드는 CHANGE_NETWORK_STATE 권한 누락으로 requestNetwork가 SecurityException을 던졌는데,
 *  runCatching이 이를 삼켜서 rtt_ms / probe_dl_mbps가 한 번도 기록되지 않았다.)
 *
 * 데이터 소모: RTT는 무시 가능, 버스트는 DL_BURST_BYTES × (세션시간/DL_INTERVAL).
 * 기본값(2MB/30초)으로 1시간 ≈ 240MB. SESSION_BYTE_CAP 도달 시 버스트만 자동 중지된다.
 */
class ActiveProbe(context: Context) {

    companion object {
        private const val TAG = "ActiveProbe"
        private val RTT_TARGETS = listOf("8.8.8.8" to 53, "1.1.1.1" to 443, "speed.cloudflare.com" to 443)
        private const val RTT_TIMEOUT_MS = 2_000
        private const val DL_INTERVAL_MS = 30_000L
        private const val DL_BURST_BYTES = 2_000_000L
        private const val DL_URL = "https://speed.cloudflare.com/__down?bytes=$DL_BURST_BYTES"
        private const val DL_TIMEOUT_MS = 15_000
        const val SESSION_BYTE_CAP = 500L * 1024 * 1024   // 500 MB
    }

    private val cm = context.applicationContext
        .getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    @Volatile private var cellularNetwork: Network? = null
    @Volatile private var requestError: String? = null
    @Volatile private var running = false
    @Volatile private var downloadEnabled = false
    @Volatile var probeBytesUsed = 0L; private set

    /** 마지막 RTT (ms). 실패/타임아웃 시 null. collect()가 매번 읽는다. */
    @Volatile var lastRttMs: Int? = null; private set
    /** 마지막으로 성공한 버스트 처리량 — 화면·알림 표시용 (CSV에는 consumeDlResult로 1행만 기록). */
    @Volatile var lastDlMbps: Double? = null; private set

    /** 사람이 읽는 상태. "정상 …" 또는 "실패 — 이유". */
    @Volatile var rttStatus = "시작 전"; private set
    @Volatile var dlStatus = "꺼짐"; private set

    private var rttTargetIdx = 0
    private var rttOk = 0; private var rttFail = 0
    private var dlOk = 0; private var dlFail = 0

    // 버스트 결과는 "완료 직후 1개 행"에만 기록하도록 consume 방식으로 전달
    @Volatile private var pendingDlMbps: Double? = null

    // RTT와 다운로드 버스트는 서로 다른 스레드에서 돈다. 한 스레드를 공유하면 최대 15초짜리
    // 버스트가 RTT tick을 막아, 하필 "부하가 걸린 순간"의 지연이 측정에서 빠진다.
    private var rttThread: HandlerThread? = null
    private var rttHandler: Handler? = null
    private var dlThread: HandlerThread? = null
    private var dlHandler: Handler? = null
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

    private val dlTick = object : Runnable {
        override fun run() {
            if (!running) return
            if (probeBytesUsed < SESSION_BYTE_CAP) measureDownload()
            else dlStatus = "중지 — 세션 한도 ${SESSION_BYTE_CAP / 1_048_576}MB 도달"
            dlHandler?.postDelayed(this, DL_INTERVAL_MS)
        }
    }

    fun start(collectIntervalMs: Long, downloadEnabled: Boolean) {
        if (running) return
        running = true
        this.downloadEnabled = downloadEnabled
        intervalMs = collectIntervalMs
        probeBytesUsed = 0L
        lastRttMs = null
        lastDlMbps = null
        pendingDlMbps = null
        requestError = null
        rttOk = 0; rttFail = 0; dlOk = 0; dlFail = 0
        rttStatus = "셀룰러 망 요청 중"
        dlStatus = if (downloadEnabled) "첫 버스트 대기 중" else "꺼짐 (스위치 OFF)"

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
            dlThread = HandlerThread("ActiveProbe-DL").also { it.start() }
            dlHandler = Handler(dlThread!!.looper).also {
                it.postDelayed(dlTick, 5_000L)   // 시작 5초 후 첫 버스트
            }
        }
    }

    fun stop() {
        running = false
        runCatching { cm.unregisterNetworkCallback(networkCallback) }
        cellularNetwork = null
        rttHandler?.removeCallbacksAndMessages(null)
        dlHandler?.removeCallbacksAndMessages(null)
        rttThread?.quitSafely()
        dlThread?.quitSafely()
        rttThread = null; rttHandler = null
        dlThread  = null; dlHandler  = null
    }

    /** 완료된 버스트 결과를 1회만 반환하고 비운다 (없으면 null). */
    fun consumeDlResult(): Double? {
        val v = pendingDlMbps
        pendingDlMbps = null
        return v
    }

    /** 화면 표시용 두 줄 요약. */
    fun statusText(): String = "RTT: $rttStatus\nDL : $dlStatus"

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

    private fun measureDownload() {
        val net = cellular()
        if (net == null) {
            dlFail++
            dlStatus = "실패 — ${noNetworkReason()}"
            return
        }
        try {
            val conn = net.openConnection(URL(DL_URL)) as HttpURLConnection
            conn.connectTimeout = DL_TIMEOUT_MS
            conn.readTimeout = DL_TIMEOUT_MS
            conn.useCaches = false
            try {
                val t0 = System.nanoTime()
                val code = conn.responseCode
                if (code != HttpURLConnection.HTTP_OK) {
                    dlFail++
                    dlStatus = "실패 — HTTP $code (성공 $dlOk / 실패 $dlFail)"
                    return
                }
                var total = 0L
                val buf = ByteArray(64 * 1024)
                conn.inputStream.use { input ->
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        total += n
                    }
                }
                val elapsedS = (System.nanoTime() - t0) / 1e9
                probeBytesUsed += total
                if (total > 0 && elapsedS > 0.05) {
                    val mbps = total * 8.0 / 1_000_000.0 / elapsedS
                    pendingDlMbps = mbps
                    lastDlMbps = mbps
                    dlOk++
                    dlStatus = String.format(Locale.US, "정상 %.1f Mbps (성공 %d / 실패 %d, %dMB 사용)",
                        mbps, dlOk, dlFail, probeBytesUsed / 1_048_576)
                } else {
                    dlFail++
                    dlStatus = "실패 — 받은 데이터 없음 (성공 $dlOk / 실패 $dlFail)"
                }
            } finally {
                conn.disconnect()
            }
        } catch (e: Exception) {
            dlFail++
            dlStatus = "실패 — ${e.javaClass.simpleName}: ${e.message} (성공 $dlOk / 실패 $dlFail)"
            Log.w(TAG, "다운로드 버스트 실패", e)
        }
    }
}
