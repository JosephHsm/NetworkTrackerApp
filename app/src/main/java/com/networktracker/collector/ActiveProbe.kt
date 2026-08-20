package com.networktracker.collector

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Handler
import android.os.HandlerThread
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.URL

/**
 * 능동 측정(active measurement) 프로브.
 *
 * TrafficStats 기반 수동 측정은 "수요"라서 망 "용량"을 못 본다(ISSUE_ANALYSIS.md 이슈 3).
 * 유튜브 스트리밍도 ABR이 버퍼를 채우면 다운로드를 멈춰서 부하가 불연속적이다.
 * 그래서 앱이 직접 부하를 만들어 측정한다.
 *
 *  1. RTT: 매 tick마다 TCP connect(8.8.8.8:53) 소요시간 측정 (~수백 바이트).
 *     핸드오버 순간의 지연 스파이크(제어평면 중단시간)가 그대로 드러난다.
 *  2. 다운로드 버스트: DL_INTERVAL_MS마다 고정 크기 HTTP 다운로드로 순간 처리량 측정.
 *     Cloudflare 공개 엔드포인트를 써서 별도 서버가 필요 없다.
 *
 * 두 측정 모두 셀룰러 네트워크에 명시적으로 바인딩한다 — Wi-Fi가 켜져 있어도
 * 셀룰러 경로를 측정하므로 wifi_active 오염 문제가 없다.
 *
 * 데이터 소모: RTT는 무시 가능, 버스트는 DL_BURST_BYTES × (세션시간/DL_INTERVAL).
 * 기본값(2MB/30초)으로 1시간 ≈ 240MB. SESSION_BYTE_CAP 도달 시 버스트만 자동 중지된다.
 */
class ActiveProbe(context: Context) {

    companion object {
        private const val RTT_HOST = "8.8.8.8"
        private const val RTT_PORT = 53
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
    @Volatile private var running = false
    @Volatile var probeBytesUsed = 0L; private set

    /** 마지막 RTT (ms). 실패/타임아웃 시 null. collect()가 매번 읽는다. */
    @Volatile var lastRttMs: Int? = null; private set

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
            dlHandler?.postDelayed(this, DL_INTERVAL_MS)
        }
    }

    fun start(collectIntervalMs: Long, downloadEnabled: Boolean) {
        if (running) return
        running = true
        intervalMs = collectIntervalMs
        probeBytesUsed = 0L
        lastRttMs = null
        pendingDlMbps = null

        runCatching {
            cm.requestNetwork(
                NetworkRequest.Builder()
                    .addTransportType(NetworkCapabilities.TRANSPORT_CELLULAR)
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    .build(),
                networkCallback
            )
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

    // ── 측정 구현 ─────────────────────────────────────────────────────────────

    private fun measureRtt() {
        val net = cellularNetwork
        lastRttMs = if (net == null) null else runCatching {
            net.socketFactory.createSocket().use { sock ->
                val t0 = System.nanoTime()
                sock.connect(InetSocketAddress(RTT_HOST, RTT_PORT), RTT_TIMEOUT_MS)
                ((System.nanoTime() - t0) / 1_000_000L).toInt()
            }
        }.getOrNull()
    }

    private fun measureDownload() {
        val net = cellularNetwork ?: return
        runCatching {
            val conn = net.openConnection(URL(DL_URL)) as HttpURLConnection
            conn.connectTimeout = DL_TIMEOUT_MS
            conn.readTimeout = DL_TIMEOUT_MS
            try {
                val t0 = System.nanoTime()
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
                if (total > 0 && elapsedS > 0.05)
                    pendingDlMbps = total * 8.0 / 1_000_000.0 / elapsedS
            } finally {
                conn.disconnect()
            }
        }
    }
}
