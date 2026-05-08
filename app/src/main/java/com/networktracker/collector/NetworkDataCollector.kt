package com.networktracker.collector

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
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

class NetworkDataCollector(private val context: Context) {

    private val tel = context.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
    private val lm  = context.getSystemService(Context.LOCATION_SERVICE)  as LocationManager

    private var prevRxBytes = TrafficStats.getTotalRxBytes()
    private var prevTxBytes = TrafficStats.getTotalTxBytes()
    private var prevTimeMs  = System.currentTimeMillis()

    @Volatile private var lastLocation: Location? = null

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
        val cb = object : TelephonyCallback(), TelephonyCallback.DisplayInfoListener {
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
        telephonyCallbackRef = cb
        tel.registerTelephonyCallback(context.mainExecutor, cb)
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
                            val id  = cell.cellIdentity.ci.toString()
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
                                    put("cell_id", id)
                                    put("pci",     cell.cellIdentity.pci)
                                    put("earfcn",  cell.cellIdentity.earfcn)
                                    put("tac",     cell.cellIdentity.tac)
                                    put("rsrp",    sig.rsrp)
                                    put("rsrq",    sig.rsrq)
                                    put("rssi",    sig.rssi)
                                    put("snr",     sig.rssnr)
                                    put("ta",      sig.timingAdvance)
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
                            val nci = identity.nci.toString()
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
                                    put("nci",      nci)
                                    put("pci",      identity.pci)
                                    put("arfcn",    identity.nrarfcn)
                                    put("tac",      identity.tac)
                                    put("ss_rsrp",  sig.ssRsrp)
                                    put("ss_rsrq",  sig.ssRsrq)
                                    put("ss_sinr",  sig.ssSinr)
                                    put("csi_rsrp", sig.csiRsrp)
                                    put("csi_rsrq", sig.csiRsrq)
                                    put("csi_sinr", sig.csiSinr)
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

    private fun hasLocation() =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
}
