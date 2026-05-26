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
import android.net.TrafficStats
import android.os.Build
import android.telephony.*
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import com.networktracker.data.NetworkRecord
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.sqrt

class NetworkDataCollector(private val context: Context) {

    private val tel       = context.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
    private val lm        = context.getSystemService(Context.LOCATION_SERVICE)  as LocationManager
    private val sensorMgr = context.getSystemService(Context.SENSOR_SERVICE)    as SensorManager

    private var prevRxBytes = TrafficStats.getTotalRxBytes()
    private var prevTxBytes = TrafficStats.getTotalTxBytes()
    private var prevTimeMs  = System.currentTimeMillis()

    @Volatile private var lastLocation: Location? = null

    // ── 가속도 센서 기반 속도 추정 ────────────────────────────────────────────
    private val linearAccelSensor = sensorMgr.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION)
    @Volatile private var imuVelocityMs  = 0f
    @Volatile private var imuLastTimeNs  = 0L

    private val imuListener = object : SensorEventListener {
        override fun onSensorChanged(event: SensorEvent) {
            // TYPE_LINEAR_ACCELERATION: 중력 제거된 선가속도 (m/s²)
            val ax = event.values[0]
            val ay = event.values[1]
            val aHoriz = sqrt((ax * ax + ay * ay).toDouble()).toFloat()
            val nowNs  = event.timestamp
            if (imuLastTimeNs > 0L) {
                val dtSec = (nowNs - imuLastTimeNs) * 1e-9f
                if (aHoriz > 0.3f) {
                    // 유의미한 가속 → 속도 적분 (최대 80 m/s = ~288 km/h 상한)
                    imuVelocityMs = (imuVelocityMs + aHoriz * dtSec).coerceAtMost(80f)
                } else {
                    // 정지 상태로 판단 → 감쇠 (drift 억제)
                    imuVelocityMs = (imuVelocityMs * (1f - dtSec * 1.5f)).coerceAtLeast(0f)
                }
            }
            imuLastTimeNs = nowNs
        }
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    }

    fun startImuSensor() {
        linearAccelSensor?.let { sensor ->
            sensorMgr.registerListener(imuListener, sensor, SensorManager.SENSOR_DELAY_GAME)
        }
    }

    fun stopImuSensor() {
        sensorMgr.unregisterListener(imuListener)
        imuVelocityMs = 0f
        imuLastTimeNs = 0L
    }

    // ── 핸드오버 / 핑퐁 감지 ─────────────────────────────────────────────────
    // 최근 10회 서빙셀 이력 (cellId, timestamp_ms)
    private val cellHistory   = ArrayDeque<Pair<String, Long>>()
    private var lastCellId    = ""

    // CellInfoListener(API 31+)가 서빙셀 변경을 감지했을 때 서비스가 즉시 수집하도록 알리는 콜백
    var onCellChangeDetected: (() -> Unit)? = null

    private fun checkHandover(newCellId: String, nowMs: Long): Pair<Boolean, Boolean> {
        if (newCellId.isEmpty() || newCellId == lastCellId) return Pair(false, false)
        // 첫 수집(lastCellId가 아직 비어 있음)은 핸드오버가 아닌 초기화로 처리
        if (lastCellId.isEmpty()) { lastCellId = newCellId; return Pair(false, false) }
        // 핑퐁: 30초 이내 같은 셀로 복귀
        val pingPong = cellHistory.any { (id, ts) -> id == newCellId && (nowMs - ts) < 30_000L }
        cellHistory.addFirst(Pair(lastCellId, nowMs))
        if (cellHistory.size > 10) cellHistory.removeLast()
        lastCellId = newCellId
        return Pair(true, pingPong)
    }

    private val gpsListener = LocationListener { loc -> lastLocation = loc }
    private val netListener = LocationListener { loc ->
        if (lastLocation == null || loc.accuracy < (lastLocation?.accuracy ?: Float.MAX_VALUE))
            lastLocation = loc
    }

    // 5G NSA 감지 상태 (API 31+: TelephonyCallback / API 30: PhoneStateListener)
    @Volatile private var displayOverrideType: String = "NONE"
    @Volatile private var displayIs5G: Boolean = false

    private var phoneStateListener: PhoneStateListener? = null  // API 30 전용
    @Volatile private var telephonyCallbackRef: Any? = null     // TelephonyCallback (API 31+)

    // ── 위치 업데이트 ──────────────────────────────────────────────────────────

    @SuppressLint("MissingPermission")
    fun startLocationUpdates() {
        if (!hasLocation()) return
        runCatching {
            listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER).forEach { p ->
                if (lm.isProviderEnabled(p))
                    lm.getLastKnownLocation(p)?.let { loc ->
                        if (lastLocation == null || loc.accuracy < (lastLocation?.accuracy ?: Float.MAX_VALUE))
                            lastLocation = loc
                    }
            }
            lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 2000L, 1f, gpsListener)
            if (lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER))
                lm.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 2000L, 1f, netListener)
        }
    }

    fun stopLocationUpdates() {
        runCatching { lm.removeUpdates(gpsListener) }
        runCatching { lm.removeUpdates(netListener) }
    }

    // ── 5G NSA 감지 리스너 ─────────────────────────────────────────────────────

    fun startTelephonyListener() {
        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> registerTelephonyCallback()
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R -> registerDisplayInfoListener()
        }
    }

    fun stopTelephonyListener() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            unregisterTelephonyCallback()
        } else {
            @Suppress("DEPRECATION")
            phoneStateListener?.let { tel.listen(it, PhoneStateListener.LISTEN_NONE) }
            phoneStateListener = null
        }
        displayOverrideType = "NONE"
        displayIs5G = false
    }

    @RequiresApi(Build.VERSION_CODES.S)
    private fun unregisterTelephonyCallback() {
        telephonyCallbackRef?.let {
            @Suppress("UNCHECKED_CAST")
            tel.unregisterTelephonyCallback(it as TelephonyCallback)
        }
        telephonyCallbackRef = null
    }

    // API 31+: TelephonyCallback (PhoneStateListener 대체)
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

            // 모뎀이 셀 정보를 업데이트할 때마다 호출됨 — 서빙셀 변경 시 즉시 수집 트리거
            override fun onCellInfoChanged(cellInfos: List<CellInfo>) {
                val newId = extractServingId(cellInfos)
                if (newId.isNotEmpty() && newId != lastCellId) {
                    onCellChangeDetected?.invoke()
                }
            }
        }
        telephonyCallbackRef = cb
        tel.registerTelephonyCallback(context.mainExecutor, cb)
    }

    // CellInfoListener 콜백 파라미터에서 서빙셀 ID만 빠르게 추출 (권한 불필요)
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

    // API 30 전용: PhoneStateListener (API 31에서 deprecated, 하위 호환 유지)
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

    // ── 데이터 수집 ────────────────────────────────────────────────────────────

    @SuppressLint("MissingPermission")
    fun collect(): NetworkRecord {
        val now = System.currentTimeMillis()
        val elapsedMs = (now - prevTimeMs).coerceAtLeast(1L)

        val curRx = TrafficStats.getTotalRxBytes()
        val curTx = TrafficStats.getTotalTxBytes()
        val rxSpeed = speedBps(prevRxBytes, curRx, elapsedMs)
        val txSpeed = speedBps(prevTxBytes, curTx, elapsedMs)
        prevRxBytes = curRx; prevTxBytes = curTx; prevTimeMs = now

        val location: Location? = lastLocation ?: runCatching {
            if (hasLocation())
                lm.getLastKnownLocation(LocationManager.GPS_PROVIDER)
                    ?: lm.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
            else null
        }.getOrNull()

        // GPS 속도로 IMU drift 보정 (GPS 가용 시 기준값으로 리셋)
        location?.takeIf { it.hasSpeed() && it.speed > 0.1f }?.let { loc ->
            imuVelocityMs = loc.speed
            imuLastTimeNs = 0L
        }
        val imuSpeed = imuVelocityMs.takeIf { it > 0f || linearAccelSensor != null }

        // 기존 변수
        var networkType         = "UNKNOWN"
        var overrideNetworkType = "NONE"
        var generation          = "UNKNOWN"
        var is5G                = false
        var is5GActual          = false
        var is5GDisplay         = false
        var nrCellSeen          = false
        var nrServingCellSeen   = false
        var servingCellId       = ""
        var rsrp: Int?          = null; var rsrq: Int?   = null
        var rssi: Int?          = null; var sinrSnr: Int? = null
        var signalLevel: Int?   = null
        val neighbors           = JSONArray()
        var neighborCount       = 0

        // 신규 변수
        var csiRsrp: Int?       = null
        var csiRsrq: Int?       = null
        var csiSinr: Int?       = null
        var servingPci: Int?    = null
        var servingFreqArfcn: Int? = null
        var servingBandStr      = ""
        var servingTac: Int?    = null
        var mcc                 = ""
        var mnc                 = ""
        var timingAdvanceLte: Int? = null
        var nrNeighborCount     = 0
        var lteNeighborCount    = 0

        runCatching {
            // dataNetworkType: 데이터 연결 RAT 기준 (voice와 별개, NSA에서도 LTE 반환)
            networkType = resolveNetworkType()
            is5GActual  = networkType == "NR(5G)"

            // TelephonyDisplayInfo 기반 NSA 5G 감지 (리스너가 미리 업데이트한 값)
            overrideNetworkType = displayOverrideType
            is5GDisplay         = displayIs5G

            if (hasLocation()) {
                tel.allCellInfo?.forEach { cell ->
                    val serving = cell.isRegistered
                    when (cell) {

                        is CellInfoLte -> {
                            val sig = cell.cellSignalStrength
                            // CI가 UNAVAILABLE이면 빈 문자열 (2147483647 또는 Long.MAX_VALUE 방지)
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
                                neighbors.put(JSONObject().apply {
                                    put("type",    "LTE")
                                    // 이웃셀 CI는 모뎀이 미제공 시 UNAVAILABLE → null로 기록
                                    putOpt("cell_id", id.ifEmpty { null })
                                    put("pci",     cell.cellIdentity.pci.toJsonOrNull())
                                    put("earfcn",  cell.cellIdentity.earfcn.toJsonOrNull())
                                    put("tac",     cell.cellIdentity.tac.toJsonOrNull())
                                    put("rsrp",    sig.rsrp.toJsonOrNull())
                                    put("rsrq",    sig.rsrq.toJsonOrNull())
                                    put("rssi",    sig.rssi.toJsonOrNull())
                                    put("snr",     sig.rssnr.toJsonOrNull())
                                    put("ta",      sig.timingAdvance.toJsonOrNull())
                                    put("level",   sig.level)
                                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                                        put("bands", cell.cellIdentity.bands.joinToString(","))
                                })
                                lteNeighborCount++
                                neighborCount++
                            }
                        }

                        is CellInfoNr -> {
                            nrCellSeen = true
                            val sig      = cell.cellSignalStrength as CellSignalStrengthNr
                            val identity = cell.cellIdentity      as CellIdentityNr
                            // NCI는 36비트 Long — UNAVAILABLE_LONG(Long.MAX_VALUE) 필터링
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
                                neighbors.put(JSONObject().apply {
                                    put("type",     "NR(5G)")
                                    putOpt("nci",      nci.ifEmpty { null })
                                    put("pci",      identity.pci.toJsonOrNull())
                                    put("arfcn",    identity.nrarfcn.toJsonOrNull())
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
                                })
                                nrNeighborCount++
                                neighborCount++
                            }
                        }

                        is CellInfoWcdma -> if (!serving) {
                            neighbors.put(JSONObject().apply {
                                put("type",   "WCDMA")
                                put("cid",    cell.cellIdentity.cid)
                                put("psc",    cell.cellIdentity.psc)
                                put("uarfcn", cell.cellIdentity.uarfcn)
                                put("dbm",    cell.cellSignalStrength.dbm)
                                put("level",  cell.cellSignalStrength.level)
                            }); neighborCount++
                        }

                        is CellInfoGsm -> if (!serving) {
                            neighbors.put(JSONObject().apply {
                                put("type",  "GSM")
                                put("cid",   cell.cellIdentity.cid)
                                put("arfcn", cell.cellIdentity.arfcn)
                                put("bsic",  cell.cellIdentity.bsic)
                                put("dbm",   cell.cellSignalStrength.dbm)
                                put("level", cell.cellSignalStrength.level)
                            }); neighborCount++
                        }
                    }
                }
            }

            // TelephonyDisplayInfo 미수신 상태에서도 allCellInfo로 NR 서빙셀 직접 감지
            is5G       = is5GActual || is5GDisplay || nrServingCellSeen
            generation = toGeneration(networkType, is5G)
        }

        val (handover, pingPong) = checkHandover(servingCellId, now)

        return NetworkRecord(
            timestamp           = now,
            latitude            = location?.latitude,
            longitude           = location?.longitude,
            gpsAccuracyM        = location?.accuracy,
            gpsSpeedMs          = location?.takeIf { it.hasSpeed() }?.speed,
            gpsBearing          = location?.takeIf { it.hasBearing() }?.bearing,
            gpsAltitude         = location?.takeIf { it.hasAltitude() }?.altitude,
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
            neighborCount       = neighborCount,
            nrNeighborCount     = nrNeighborCount,
            lteNeighborCount    = lteNeighborCount,
            imuSpeedMs          = imuSpeed,
            handoverDetected    = handover,
            pingPongDetected    = pingPong,
            neighborsJson       = neighbors.toString()
        )
    }

    // tel.dataNetworkType: 데이터 연결 기준 RAT (tel.networkType보다 정확, API 30에서 deprecated된 networkType 대체)
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
        is5G                                                                     -> "5G"
        type == "LTE"                                                            -> "4G"
        type in setOf("HSPA+", "HSPA", "HSUPA", "HSDPA", "UMTS", "TD-SCDMA")  -> "3G"
        type in setOf("EDGE", "GPRS", "GSM")                                    -> "2G"
        else                                                                     -> "UNKNOWN"
    }

    private fun speedBps(prev: Long, cur: Long, elapsedMs: Long): Long {
        if (prev < 0 || cur < 0 || cur < prev) return 0L
        return (cur - prev) * 1000L / elapsedMs
    }

    private fun Int.valid(): Int? =
        takeUnless { it == Int.MAX_VALUE || it == Int.MIN_VALUE || it == CellInfo.UNAVAILABLE }

    // 이웃셀 JSON 저장용: UNAVAILABLE이면 JSONObject.NULL 반환
    private fun Int.toJsonOrNull(): Any =
        if (this == Int.MAX_VALUE || this == Int.MIN_VALUE || this == CellInfo.UNAVAILABLE)
            JSONObject.NULL else this

    // UNAVAILABLE인 Int → 빈 문자열 (serving cell ID 등 String 필드용)
    private fun Int.validToString(): String =
        if (this == Int.MAX_VALUE || this == Int.MIN_VALUE || this == CellInfo.UNAVAILABLE)
            "" else this.toString()

    // NR NCI는 Long — UNAVAILABLE_LONG(= Long.MAX_VALUE) 필터링
    private fun Long.validToString(): String =
        if (this == Long.MAX_VALUE || this == Long.MIN_VALUE) "" else this.toString()

    private fun hasLocation() =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
}
