package com.networktracker.collector

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject

/**
 * 주변 Wi-Fi AP(BSSID + RSSI)를 주기적으로 스캔해서 지하철역 핑거프린트를 남긴다.
 *
 * 용도: GPS가 끊긴 지하 구간의 사후 위치 복원.
 *  - 지하철역은 AP가 촘촘해서 BSSID 목록만으로 역 식별이 가능하다.
 *  - 수집된 BSSID+RSSI는 Google Geolocation API 후처리 입력으로도 쓸 수 있다.
 *
 * Android 스캔 스로틀링 주의: 포그라운드 앱은 2분당 4회까지만 능동 스캔이 허용된다.
 * 그래서 startScan()은 SCAN_REQUEST_INTERVAL_MS(30초)마다만 요청하고,
 * 매 수집 tick에는 시스템에 캐시된 scanResults를 그대로 읽는다
 * (다른 앱/OS가 수행한 스캔 결과도 공유되므로 실제 갱신 주기는 더 짧을 수 있다).
 *
 * 요구 사항: Wi-Fi ON 또는 설정의 "Wi-Fi 검색 항상 허용" + 위치 권한/위치 서비스 ON.
 */
class WifiScanner(private val context: Context) {

    companion object {
        private const val SCAN_REQUEST_INTERVAL_MS = 30_000L
        private const val MAX_APS_IN_JSON = 15
    }

    data class Snapshot(val apCount: Int, val ageS: Int, val json: String)

    private val wifi = context.applicationContext
        .getSystemService(Context.WIFI_SERVICE) as WifiManager
    private val handler = Handler(Looper.getMainLooper())
    private var running = false

    private val scanTick = object : Runnable {
        override fun run() {
            if (!running) return
            // 스로틀링 초과 시 false 반환 — 캐시된 결과를 계속 쓰면 되므로 무시
            @Suppress("DEPRECATION")
            runCatching { wifi.startScan() }
            handler.postDelayed(this, SCAN_REQUEST_INTERVAL_MS)
        }
    }

    fun start() {
        if (running) return
        running = true
        handler.post(scanTick)
    }

    fun stop() {
        running = false
        handler.removeCallbacks(scanTick)
    }

    /** 현재 캐시된 스캔 결과를 스냅샷으로 반환. 권한 없음/결과 없음이면 null. */
    @SuppressLint("MissingPermission")
    fun snapshot(): Snapshot? {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED) return null
        val results = runCatching { wifi.scanResults }.getOrNull() ?: return null
        if (results.isEmpty()) return null

        // ScanResult.timestamp = 부팅 이후 경과 마이크로초 — 최신 결과 기준으로 나이 계산
        val newestUs = results.maxOf { it.timestamp }
        val ageS = ((SystemClock.elapsedRealtime() - newestUs / 1000L) / 1000L)
            .coerceAtLeast(0L).toInt()

        val top = results.sortedByDescending { it.level }.take(MAX_APS_IN_JSON)
        val arr = JSONArray()
        top.forEach { r ->
            arr.put(JSONObject().apply {
                put("b", r.BSSID)                       // MAC — 핑거프린트 키
                put("s", r.SSID.take(32))               // 역 식별에 유용 (예: 통신사 지하철 SSID)
                put("r", r.level)                       // RSSI (dBm)
                put("f", r.frequency)                   // MHz
            })
        }
        return Snapshot(results.size, ageS, arr.toString())
    }
}
