package com.networktracker.collector

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Looper
import com.google.android.gms.location.FusedLocationProviderClient
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.TrafficStats
import android.os.Build
import android.os.SystemClock
import android.telephony.*
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import com.networktracker.data.NetworkRecord
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.sqrt

class NetworkDataCollector(private val context: Context) {

    private val tel       = context.getSystemService(Context.TELEPHONY_SERVICE)     as TelephonyManager
    private val lm        = context.getSystemService(Context.LOCATION_SERVICE)      as LocationManager
    private val sensorMgr = context.getSystemService(Context.SENSOR_SERVICE)        as SensorManager
    private val cm        = context.getSystemService(Context.CONNECTIVITY_SERVICE)  as ConnectivityManager

    // 수집 활동 태그 (세션 시작 전 MainActivity에서 설정)
    var activityTag: String = ""
    var collectTrigger: String = "periodic"   // 서비스가 collect() 호출 전에 설정

    // 전체 Rx/Tx (Wi-Fi + 셀룰러)
    private var prevRxBytes       = TrafficStats.getTotalRxBytes()
    private var prevTxBytes       = TrafficStats.getTotalTxBytes()
    // 셀룰러 전용 Rx/Tx
    private var prevMobileRxBytes = TrafficStats.getMobileRxBytes()
    private var prevMobileTxBytes = TrafficStats.getMobileTxBytes()
    private var prevTimeMs        = System.currentTimeMillis()

    @Volatile private var lastLocation: Location? = null
    @Volatile private var locationProvider = "none"  // 마지막 위치를 준 provider 이름
    @Volatile private var locationTimeMs   = 0L      // 마지막 위치 수신 시각

    private val fusedClient: FusedLocationProviderClient =
        LocationServices.getFusedLocationProviderClient(context)
    @Volatile private var usingFused = false

    // ── 가속도 센서 기반 속도 추정 ────────────────────────────────────────────
    private val linearAccelSensor = sensorMgr.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION)
    @Volatile private var imuVelocityMs = 0f
    @Volatile private var imuLastTimeNs = 0L

    private val imuListener = object : SensorEventListener {
        override fun onSensorChanged(event: SensorEvent) {
            val ax = event.values[0]; val ay = event.values[1]
            val aHoriz = sqrt((ax * ax + ay * ay).toDouble()).toFloat()
            val nowNs  = event.timestamp
            if (imuLastTimeNs > 0L) {
                val dtSec = (nowNs - imuLastTimeNs) * 1e-9f
                imuVelocityMs = if (aHoriz > 0.3f)
                    (imuVelocityMs + aHoriz * dtSec).coerceAtMost(80f)
                else
                    (imuVelocityMs * (1f - dtSec * 1.5f)).coerceAtLeast(0f)
            }
            imuLastTimeNs = nowNs
        }
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    }

    fun startImuSensor() {
        linearAccelSensor?.let { sensorMgr.registerListener(imuListener, it, SensorManager.SENSOR_DELAY_GAME) }
    }

    fun stopImuSensor() {
        sensorMgr.unregisterListener(imuListener)
        imuVelocityMs = 0f; imuLastTimeNs = 0L
    }

    // ── 핸드오버 / 핑퐁 감지 ─────────────────────────────────────────────────
    private data class HandoverResult(
        val handover: Boolean,
        val pingPong: Boolean,
        val prevCellId: String,
        val prevRsrp: Int?,
        val prevRsrq: Int?
    )

    private val cellHistory       = ArrayDeque<Pair<String, Long>>()
    private var lastCellId        = ""
    private var lastRsrp: Int?    = null
    private var lastRsrq: Int?    = null
    private var lastHandoverMs    = 0L           // cell_duration_s 계산용
    private var prevSampleRsrp: Int? = null      // rsrp_delta 계산용
    private var prevSampleSinr: Int? = null      // sinr_delta 계산용

    // 서빙셀 변경 시 서비스가 즉시 수집하도록 알리는 콜백 (API 31+)
    var onCellChangeDetected: (() -> Unit)? = null

    private fun checkHandover(newCellId: String, nowMs: Long, curRsrp: Int?, curRsrq: Int?): HandoverResult {
        if (newCellId.isEmpty() || newCellId == lastCellId) {
            lastRsrp = curRsrp; lastRsrq = curRsrq
            return HandoverResult(false, false, "", null, null)
        }
        if (lastCellId.isEmpty()) {
            lastCellId = newCellId; lastRsrp = curRsrp; lastRsrq = curRsrq
            return HandoverResult(false, false, "", null, null)
        }
        val pingPong = cellHistory.any { (id, ts) -> id == newCellId && (nowMs - ts) < 30_000L }
        cellHistory.addFirst(Pair(lastCellId, nowMs))
        // 개수 제한 대신 30초 이상 지난 항목 제거 — 지하철 고빈도 HO에서 핑퐁 누락 방지
        while (cellHistory.isNotEmpty() && (nowMs - cellHistory.last().second) > 30_000L)
            cellHistory.removeLast()
        lastHandoverMs = nowMs
        val result = HandoverResult(true, pingPong, lastCellId, lastRsrp, lastRsrq)
        lastCellId = newCellId; lastRsrp = curRsrp; lastRsrq = curRsrq
        return result
    }

    private val gpsListener = LocationListener { loc ->
        lastLocation = loc
        locationProvider = "gps"
        locationTimeMs   = System.currentTimeMillis()
    }
    private val netListener = LocationListener { loc ->
        if (lastLocation == null || loc.accuracy < (lastLocation?.accuracy ?: Float.MAX_VALUE)) {
            lastLocation = loc
            locationProvider = "network"
            locationTimeMs   = System.currentTimeMillis()
        }
    }
    private val locationCallback = object : LocationCallback() {
        override fun onLocationResult(result: LocationResult) {
            val loc = result.lastLocation ?: return
            lastLocation = loc
            locationProvider = "fused"
            locationTimeMs   = System.currentTimeMillis()
        }
    }

    @Volatile private var displayOverrideType = "NONE"
    @Volatile private var displayIs5G         = false

    @Suppress("DEPRECATION")
    private var phoneStateListener: PhoneStateListener? = null
    @Volatile private var telephonyCallbackRef: Any? = null

    // ── 위치 업데이트 ─────────────────────────────────────────────────────────

    @SuppressLint("MissingPermission")
    fun startLocationUpdates() {
        if (!hasLocation()) return

        // 시작 직후 쓸 마지막 알려진 위치를 미리 채워 둠
        runCatching {
            listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER).forEach { p ->
                if (lm.isProviderEnabled(p))
                    lm.getLastKnownLocation(p)?.let { loc ->
                        if (lastLocation == null || loc.accuracy < (lastLocation?.accuracy ?: Float.MAX_VALUE)) {
                            lastLocation     = loc
                            locationProvider = p
                            locationTimeMs   = System.currentTimeMillis()
                        }
                    }
            }
        }

        // FusedLocationProviderClient (GPS + Wi-Fi + 기지국 융합, OS가 자동 선택)
        runCatching {
            val req = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 2000L)
                .setMinUpdateIntervalMillis(1000L)
                .build()
            fusedClient.requestLocationUpdates(req, locationCallback, Looper.getMainLooper())
            fusedClient.lastLocation.addOnSuccessListener { loc ->
                if (loc != null && (lastLocation == null || loc.accuracy < (lastLocation?.accuracy ?: Float.MAX_VALUE))) {
                    lastLocation     = loc
                    locationProvider = "fused"
                    locationTimeMs   = System.currentTimeMillis()
                }
            }
            usingFused = true
        }.onFailure {
            // Google Play Services 없는 기기 — 구형 LocationManager로 폴백
            usingFused = false
            runCatching {
                lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 2000L, 1f, gpsListener)
                if (lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER))
                    lm.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 2000L, 1f, netListener)
            }
        }
    }

    fun stopLocationUpdates() {
        if (usingFused) runCatching { fusedClient.removeLocationUpdates(locationCallback) }
        runCatching { lm.removeUpdates(gpsListener) }
        runCatching { lm.removeUpdates(netListener) }
        usingFused = false
    }

    // ── 5G NSA 감지 리스너 ────────────────────────────────────────────────────

    fun startTelephonyListener() {
        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> registerTelephonyCallback()
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R -> registerDisplayInfoListener()
        }
    }

    fun stopTelephonyListener() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) unregisterTelephonyCallback()
        else {
            @Suppress("DEPRECATION")
            phoneStateListener?.let { tel.listen(it, PhoneStateListener.LISTEN_NONE) }
            phoneStateListener = null
        }
        displayOverrideType = "NONE"; displayIs5G = false
    }

    @RequiresApi(Build.VERSION_CODES.S)
    private fun unregisterTelephonyCallback() {
        telephonyCallbackRef?.let {
            @Suppress("UNCHECKED_CAST")
            tel.unregisterTelephonyCallback(it as TelephonyCallback)
        }
        telephonyCallbackRef = null
    }

    @RequiresApi(Build.VERSION_CODES.S)
    private fun registerTelephonyCallback() {
        val cb = object : TelephonyCallback(),
            TelephonyCallback.DisplayInfoListener,
            TelephonyCallback.CellInfoListener {

            override fun onDisplayInfoChanged(info: TelephonyDisplayInfo) {
                displayOverrideType = when (info.overrideNetworkType) {
                    TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_NSA      -> "NR_NSA"
                    TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_ADVANCED -> "NR_ADVANCED"
                    TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_LTE_CA      -> "LTE_CA"
                    else                                                    -> "NONE"
                }
                displayIs5G =
                    info.overrideNetworkType == TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_NSA ||
                    info.overrideNetworkType == TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_ADVANCED
            }

            override fun onCellInfoChanged(cellInfos: List<CellInfo>) {
                val newId = extractServingId(cellInfos)
                if (newId.isNotEmpty() && newId != lastCellId) onCellChangeDetected?.invoke()
            }
        }
        telephonyCallbackRef = cb
        tel.registerTelephonyCallback(context.mainExecutor, cb)
    }

    private fun extractServingId(cells: List<CellInfo>): String {
        for (cell in cells) {
            if (!cell.isRegistered) continue
            return when (cell) {
                is CellInfoLte -> cell.cellIdentity.ci.validToString()
                is CellInfoNr  -> (cell.cellIdentity as CellIdentityNr).nci.validToString()
                else           -> ""
            }
        }
        return ""
    }

    @RequiresApi(Build.VERSION_CODES.R)
    @Suppress("DEPRECATION")
    private fun registerDisplayInfoListener() {
        val listener = object : PhoneStateListener() {
            override fun onDisplayInfoChanged(info: TelephonyDisplayInfo) {
                displayOverrideType = when (info.overrideNetworkType) {
                    TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_NSA      -> "NR_NSA"
                    TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_ADVANCED -> "NR_ADVANCED"
                    TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_LTE_CA      -> "LTE_CA"
                    else                                                    -> "NONE"
                }
                displayIs5G =
                    info.overrideNetworkType == TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_NSA ||
                    info.overrideNetworkType == TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_ADVANCED
            }
        }
        phoneStateListener = listener
        tel.listen(listener, PhoneStateListener.LISTEN_DISPLAY_INFO_CHANGED)
    }

    // ── 데이터 수집 ───────────────────────────────────────────────────────────

    @SuppressLint("MissingPermission")
    fun collect(): NetworkRecord {
        val now       = System.currentTimeMillis()
        val elapsedMs = (now - prevTimeMs).coerceAtLeast(1L)

        // 전체 Rx/Tx
        val curRx    = TrafficStats.getTotalRxBytes()
        val curTx    = TrafficStats.getTotalTxBytes()
        val rxSpeed  = speedBps(prevRxBytes, curRx, elapsedMs)
        val txSpeed  = speedBps(prevTxBytes, curTx, elapsedMs)
        prevRxBytes  = curRx; prevTxBytes = curTx

        // 셀룰러 전용 Rx/Tx — getMobileRxBytes() 미지원 단말은 -1 반환
        val curMobileRx      = TrafficStats.getMobileRxBytes()
        val curMobileTx      = TrafficStats.getMobileTxBytes()
        val mobileRxSpeed    = if (curMobileRx >= 0 && prevMobileRxBytes >= 0)
            speedBps(prevMobileRxBytes, curMobileRx, elapsedMs) else 0L
        val mobileTxSpeed    = if (curMobileTx >= 0 && prevMobileTxBytes >= 0)
            speedBps(prevMobileTxBytes, curMobileTx, elapsedMs) else 0L
        if (curMobileRx >= 0) prevMobileRxBytes = curMobileRx
        if (curMobileTx >= 0) prevMobileTxBytes = curMobileTx

        prevTimeMs = now

        val wifiActive = isWifiActive()

        // location_source / location_age_s 계산
        val locationAgeMs   = if (locationTimeMs > 0L) now - locationTimeMs else null
        val effectiveSource = when {
            lastLocation == null                               -> "none"
            locationAgeMs != null && locationAgeMs > 60_000L  -> "stale_$locationProvider"
            else                                               -> locationProvider
        }
        val locationAgeSec  = locationAgeMs?.let { (it / 1000L).toInt() }

        val location: Location? = lastLocation

        location?.takeIf { it.hasSpeed() && it.speed > 0.1f }?.let { loc ->
            imuVelocityMs = loc.speed
            imuLastTimeNs = 0L
        }
        val imuSpeed = imuVelocityMs.takeIf { it > 0f || linearAccelSensor != null }

        var networkType         = "UNKNOWN"
        var overrideNetworkType = "NONE"
        var generation          = "UNKNOWN"
        var is5G                = false
        var is5GActual          = false
        var is5GDisplay         = false
        var nrCellSeen          = false
        var nrServingCellSeen   = false
        var servingCellId       = ""
        var rsrp: Int?          = null; var rsrq: Int?    = null
        var rssi: Int?          = null; var sinrSnr: Int? = null
        var signalLevel: Int?   = null
        var csiRsrp: Int?       = null; var csiRsrq: Int? = null; var csiSinr: Int? = null
        var servingPci: Int?    = null; var servingFreqArfcn: Int? = null
        var servingBandStr      = ""
        var servingTac: Int?    = null
        var mcc                 = ""; var mnc = ""
        var timingAdvanceLte: Int? = null
        var nrNeighborCount     = 0; var lteNeighborCount = 0; var neighborCount = 0

        // 이웃셀을 (rsrp, JSONObject) 쌍으로 수집한 뒤 RSRP 내림차순 정렬
        data class NbrEntry(val rsrp: Int, val json: JSONObject)
        val nbrList = mutableListOf<NbrEntry>()

        // 이웃셀 최강 RSRP 추적
        var bestNbrRsrp: Int? = null; var bestNbrPci: Int? = null; var bestNbrArfcn: Int? = null

        // allCellInfo 데이터 신선도 — 클수록 stale (정상: <5000ms)
        // API 30+: timestampMillis(ms), API 29: getTimeStamp()(ns, deprecated)
        val cellInfoAgeMs: Long? = if (hasLocation()) runCatching {
            val firstCell = tel.allCellInfo?.firstOrNull()
            if (firstCell != null) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    SystemClock.elapsedRealtime() - firstCell.timestampMillis
                } else {
                    @Suppress("DEPRECATION")
                    (SystemClock.elapsedRealtimeNanos() - firstCell.timeStamp) / 1_000_000L
                }
            } else null
        }.getOrNull() else null

        runCatching {
            networkType = resolveNetworkType()
            is5GActual  = networkType == "NR(5G)"
            overrideNetworkType = displayOverrideType
            is5GDisplay         = displayIs5G

            if (hasLocation()) {
                tel.allCellInfo?.forEach { cell ->
                    val serving = cell.isRegistered
                    when (cell) {
                        is CellInfoLte -> {
                            val sig = cell.cellSignalStrength
                            val id  = cell.cellIdentity.ci.validToString()
                            if (serving) {
                                servingCellId    = id
                                rsrp             = sig.rsrp.valid()
                                rsrq             = sig.rsrq.valid()
                                rssi             = sig.rssi.valid()
                                sinrSnr          = sig.rssnr.valid()
                                signalLevel      = sig.level
                                timingAdvanceLte = sig.timingAdvance.valid()
                                servingPci       = cell.cellIdentity.pci.valid()
                                servingFreqArfcn = cell.cellIdentity.earfcn.valid()
                                servingTac       = cell.cellIdentity.tac.valid()
                                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                                    servingBandStr = cell.cellIdentity.bands.joinToString(",")
                                cell.cellIdentity.mccString?.let { mcc = it }
                                cell.cellIdentity.mncString?.let { mnc = it }
                            } else {
                                val nRsrp   = sig.rsrp.valid()
                                val nPci    = cell.cellIdentity.pci.valid()
                                val nEarfcn = cell.cellIdentity.earfcn.valid()
                                // 최강 이웃셀 갱신
                                if (nRsrp != null && (bestNbrRsrp == null || nRsrp > bestNbrRsrp!!)) {
                                    bestNbrRsrp = nRsrp; bestNbrPci = nPci; bestNbrArfcn = nEarfcn
                                }
                                val json = JSONObject().apply {
                                    put("type",   "LTE")
                                    putOpt("cell_id", id.ifEmpty { null })
                                    put("pci",    nPci.toJsonOrNull())
                                    put("earfcn", nEarfcn.toJsonOrNull())
                                    put("tac",    cell.cellIdentity.tac.toJsonOrNull())
                                    put("rsrp",   sig.rsrp.toJsonOrNull())
                                    put("rsrq",   sig.rsrq.toJsonOrNull())
                                    put("rssi",   sig.rssi.toJsonOrNull())
                                    put("snr",    sig.rssnr.toJsonOrNull())
                                    put("ta",     sig.timingAdvance.toJsonOrNull())
                                    put("level",  sig.level)
                                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                                        put("bands", cell.cellIdentity.bands.joinToString(","))
                                }
                                nbrList.add(NbrEntry(nRsrp ?: Int.MIN_VALUE, json))
                                lteNeighborCount++; neighborCount++
                            }
                        }
                        is CellInfoNr -> {
                            nrCellSeen = true
                            val sig      = cell.cellSignalStrength as CellSignalStrengthNr
                            val identity = cell.cellIdentity      as CellIdentityNr
                            val nci = identity.nci.validToString()
                            if (serving) {
                                nrServingCellSeen = true
                                servingCellId    = nci
                                rsrp             = sig.ssRsrp.valid()
                                rsrq             = sig.ssRsrq.valid()
                                sinrSnr          = sig.ssSinr.valid()
                                signalLevel      = sig.level
                                csiRsrp          = sig.csiRsrp.valid()
                                csiRsrq          = sig.csiRsrq.valid()
                                csiSinr          = sig.csiSinr.valid()
                                servingPci       = identity.pci.valid()
                                servingFreqArfcn = identity.nrarfcn.valid()
                                servingTac       = identity.tac.valid()
                                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                                    servingBandStr = identity.bands.joinToString(",")
                                identity.mccString?.let { mcc = it }
                                identity.mncString?.let { mnc = it }
                            } else {
                                val nRsrp   = sig.ssRsrp.valid()
                                val nPci    = identity.pci.valid()
                                val nArfcn  = identity.nrarfcn.valid()
                                if (nRsrp != null && (bestNbrRsrp == null || nRsrp > bestNbrRsrp!!)) {
                                    bestNbrRsrp = nRsrp; bestNbrPci = nPci; bestNbrArfcn = nArfcn
                                }
                                val json = JSONObject().apply {
                                    put("type",     "NR(5G)")
                                    putOpt("nci",      nci.ifEmpty { null })
                                    put("pci",      nPci.toJsonOrNull())
                                    put("arfcn",    nArfcn.toJsonOrNull())
                                    put("tac",      identity.tac.toJsonOrNull())
                                    put("ss_rsrp",  sig.ssRsrp.toJsonOrNull())
                                    put("ss_rsrq",  sig.ssRsrq.toJsonOrNull())
                                    put("ss_sinr",  sig.ssSinr.toJsonOrNull())
                                    put("csi_rsrp", sig.csiRsrp.toJsonOrNull())
                                    put("csi_rsrq", sig.csiRsrq.toJsonOrNull())
                                    put("csi_sinr", sig.csiSinr.toJsonOrNull())
                                    put("level",    sig.level)
                                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                                        put("bands", identity.bands.joinToString(","))
                                }
                                nbrList.add(NbrEntry(nRsrp ?: Int.MIN_VALUE, json))
                                nrNeighborCount++; neighborCount++
                            }
                        }
                        is CellInfoWcdma -> if (!serving) {
                            nbrList.add(NbrEntry(cell.cellSignalStrength.dbm, JSONObject().apply {
                                put("type",   "WCDMA")
                                put("cid",    cell.cellIdentity.cid)
                                put("psc",    cell.cellIdentity.psc)
                                put("uarfcn", cell.cellIdentity.uarfcn)
                                put("dbm",    cell.cellSignalStrength.dbm)
                                put("level",  cell.cellSignalStrength.level)
                            })); neighborCount++
                        }
                        is CellInfoGsm -> if (!serving) {
                            nbrList.add(NbrEntry(cell.cellSignalStrength.dbm, JSONObject().apply {
                                put("type",  "GSM")
                                put("cid",   cell.cellIdentity.cid)
                                put("arfcn", cell.cellIdentity.arfcn)
                                put("bsic",  cell.cellIdentity.bsic)
                                put("dbm",   cell.cellSignalStrength.dbm)
                                put("level", cell.cellSignalStrength.level)
                            })); neighborCount++
                        }
                    }
                }
            }
            is5G       = is5GActual || is5GDisplay || nrServingCellSeen
            generation = toGeneration(networkType, is5G)
        }

        // 이웃셀 RSRP 내림차순 정렬 후 JSONArray 구성
        nbrList.sortByDescending { it.rsrp }
        val neighbors = JSONArray().also { arr -> nbrList.forEach { arr.put(it.json) } }

        val ho = checkHandover(servingCellId, now, rsrp, rsrq)

        // 이전 샘플 대비 변화량 (핸드오버 직후는 셀이 바뀌므로 null)
        val rsrpDelta  = if (!ho.handover) rsrp?.let { r -> prevSampleRsrp?.let { p -> r - p } } else null
        val sinrDelta  = if (!ho.handover) sinrSnr?.let { s -> prevSampleSinr?.let { p -> s - p } } else null
        prevSampleRsrp = rsrp
        prevSampleSinr = sinrSnr

        val cellDurationS = if (lastHandoverMs > 0L) (now - lastHandoverMs) / 1000L else 0L
        val hoCount30s    = cellHistory.count { (_, ts) -> (now - ts) < 30_000L }

        return NetworkRecord(
            timestamp           = now,
            activity            = activityTag,
            latitude            = location?.latitude,
            longitude           = location?.longitude,
            gpsAccuracyM        = location?.accuracy,
            gpsSpeedMs          = location?.takeIf { it.hasSpeed() }?.speed,
            gpsBearing          = location?.takeIf { it.hasBearing() }?.bearing,
            gpsAltitude         = location?.takeIf { it.hasAltitude() }?.altitude,
            locationSource      = effectiveSource,
            locationAgeS        = locationAgeSec,
            networkType         = networkType,
            overrideNetworkType = overrideNetworkType,
            generation          = generation,
            is5G                = is5G,
            is5GActual          = is5GActual,
            is5GDisplay         = is5GDisplay,
            nrCellSeen          = nrCellSeen,
            nrServingCellSeen   = nrServingCellSeen,
            servingCellId       = servingCellId,
            servingPci          = servingPci,
            servingFreqArfcn    = servingFreqArfcn,
            servingBandStr      = servingBandStr,
            servingTac          = servingTac,
            mcc                 = mcc,
            mnc                 = mnc,
            rsrp                = rsrp,
            rsrq                = rsrq,
            rssi                = rssi,
            sinrSnr             = sinrSnr,
            signalLevel         = signalLevel,
            timingAdvanceLte    = timingAdvanceLte,
            csiRsrp             = csiRsrp,
            csiRsrq             = csiRsrq,
            csiSinr             = csiSinr,
            rxSpeedBps          = rxSpeed,
            txSpeedBps          = txSpeed,
            mobileRxSpeedBps    = mobileRxSpeed,
            mobileTxSpeedBps    = mobileTxSpeed,
            wifiActive          = wifiActive,
            neighborCount       = neighborCount,
            nrNeighborCount     = nrNeighborCount,
            lteNeighborCount    = lteNeighborCount,
            bestNbrRsrp         = bestNbrRsrp,
            bestNbrPci          = bestNbrPci,
            bestNbrArfcn        = bestNbrArfcn,
            imuSpeedMs          = imuSpeed,
            handoverDetected    = ho.handover,
            pingPongDetected    = ho.pingPong,
            collectTrigger      = collectTrigger,
            cellDurationS       = cellDurationS,
            hoCount30s          = hoCount30s,
            rsrpDelta           = rsrpDelta,
            sinrDelta           = sinrDelta,
            cellInfoAgeMs       = cellInfoAgeMs,
            prevServingCellId   = ho.prevCellId,
            prevRsrp            = ho.prevRsrp,
            prevRsrq            = ho.prevRsrq,
            neighborsJson       = neighbors.toString()
        )
    }

    // ── 헬퍼 ─────────────────────────────────────────────────────────────────

    /** 다음 collect()가 신선한 allCellInfo를 쓰도록 모뎀에 갱신 요청 (API 29+, 비동기) */
    @SuppressLint("MissingPermission")
    fun refreshCellInfo() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && hasLocation()) {
            runCatching {
                tel.requestCellInfoUpdate(context.mainExecutor,
                    object : TelephonyManager.CellInfoCallback() {
                        override fun onCellInfo(cellInfos: MutableList<CellInfo>) {}
                        override fun onError(errorCode: Int, detail: Throwable?) {}
                    })
            }
        }
    }

    private fun isWifiActive(): Boolean {
        val caps = cm.getNetworkCapabilities(cm.activeNetwork ?: return false) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
    }

    @SuppressLint("MissingPermission")
    private fun resolveNetworkType(): String = runCatching {
        when (tel.dataNetworkType) {
            TelephonyManager.NETWORK_TYPE_NR       -> "NR(5G)"
            TelephonyManager.NETWORK_TYPE_LTE      -> "LTE"
            TelephonyManager.NETWORK_TYPE_HSPAP    -> "HSPA+"
            TelephonyManager.NETWORK_TYPE_HSPA     -> "HSPA"
            TelephonyManager.NETWORK_TYPE_HSUPA    -> "HSUPA"
            TelephonyManager.NETWORK_TYPE_HSDPA    -> "HSDPA"
            TelephonyManager.NETWORK_TYPE_UMTS     -> "UMTS"
            TelephonyManager.NETWORK_TYPE_EDGE     -> "EDGE"
            TelephonyManager.NETWORK_TYPE_GPRS     -> "GPRS"
            TelephonyManager.NETWORK_TYPE_GSM      -> "GSM"
            TelephonyManager.NETWORK_TYPE_TD_SCDMA -> "TD-SCDMA"
            TelephonyManager.NETWORK_TYPE_IWLAN    -> "IWLAN"
            else -> "UNKNOWN"
        }
    }.getOrDefault("UNKNOWN")

    private fun toGeneration(type: String, is5G: Boolean) = when {
        is5G                                                                    -> "5G"
        type == "LTE"                                                           -> "4G"
        type in setOf("HSPA+", "HSPA", "HSUPA", "HSDPA", "UMTS", "TD-SCDMA") -> "3G"
        type in setOf("EDGE", "GPRS", "GSM")                                   -> "2G"
        else                                                                    -> "UNKNOWN"
    }

    private fun speedBps(prev: Long, cur: Long, elapsedMs: Long): Long {
        if (prev < 0 || cur < 0 || cur < prev) return 0L
        return (cur - prev) * 1000L / elapsedMs
    }

    private fun Int.valid(): Int? =
        takeUnless { it == Int.MAX_VALUE || it == Int.MIN_VALUE || it == CellInfo.UNAVAILABLE }

    // nullable/non-nullable 모두 처리하는 단일 버전
    private fun Int?.toJsonOrNull(): Any =
        if (this == null || this == Int.MAX_VALUE || this == Int.MIN_VALUE || this == CellInfo.UNAVAILABLE)
            JSONObject.NULL else this

    private fun Int.validToString(): String =
        if (this == Int.MAX_VALUE || this == Int.MIN_VALUE || this == CellInfo.UNAVAILABLE)
            "" else this.toString()

    private fun Long.validToString(): String =
        if (this == Long.MAX_VALUE || this == Long.MIN_VALUE) "" else this.toString()

    private fun hasLocation() =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
}
