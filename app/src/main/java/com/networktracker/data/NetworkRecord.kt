package com.networktracker.data

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class NetworkRecord(
    val timestamp: Long = System.currentTimeMillis(),
    val activity: String = "",               // ML 레이블: walking/subway/car/home 등

    val latitude: Double? = null,
    val longitude: Double? = null,
    val gpsAccuracyM: Float? = null,
    val gpsSpeedMs: Float? = null,
    val gpsBearing: Float? = null,
    val gpsAltitude: Double? = null,
    // 위치 소스 및 신선도 — 지하 구간 필터링에 사용
    // fused: GMS 융합(GPS+WiFi+기지국), gps/network: 폴백 LocationManager
    // stale_*: 60초 이상 갱신 없음 (GPS가 얼어붙은 상태)
    val locationSource: String = "none",
    val locationAgeS: Int? = null,

    val networkType: String = "UNKNOWN",
    val overrideNetworkType: String = "NONE",
    val generation: String = "UNKNOWN",
    val is5G: Boolean = false,
    val is5GActual: Boolean = false,
    val is5GDisplay: Boolean = false,
    val nrCellSeen: Boolean = false,
    val nrServingCellSeen: Boolean = false,

    val servingCellId: String = "",
    val servingPci: Int? = null,
    val servingFreqArfcn: Int? = null,
    val servingBandStr: String = "",
    val servingTac: Int? = null,
    val mcc: String = "",
    val mnc: String = "",

    val rsrp: Int? = null,
    val rsrq: Int? = null,
    val rssi: Int? = null,
    val sinrSnr: Int? = null,
    val signalLevel: Int? = null,
    val timingAdvanceLte: Int? = null,
    val csiRsrp: Int? = null,
    val csiRsrq: Int? = null,
    val csiSinr: Int? = null,

    // 전체 처리량 (Wi-Fi + 셀룰러 합산, TrafficStats.getTotalRx 기반)
    val rxSpeedBps: Long = 0L,
    val txSpeedBps: Long = 0L,
    // 셀룰러 전용 처리량 (TrafficStats.getMobileRx 기반 — 신호↔처리량 분석에 사용)
    val mobileRxSpeedBps: Long = 0L,
    val mobileTxSpeedBps: Long = 0L,
    val wifiActive: Boolean = false,         // Wi-Fi 연결 여부 (총 Rx/Tx 오염 판단)

    val neighborCount: Int = 0,
    val nrNeighborCount: Int = 0,
    val lteNeighborCount: Int = 0,
    // 이웃셀 최강 신호 요약 — JSON 파싱 없이 ML 피처로 직접 사용
    val bestNbrRsrp: Int? = null,
    val bestNbrPci: Int? = null,
    val bestNbrArfcn: Int? = null,

    val imuSpeedMs: Float? = null,
    val handoverDetected: Boolean = false,
    val pingPongDetected: Boolean = false,
    val collectTrigger: String = "periodic",     // "periodic"(5초) | "handover"(이벤트)
    val cellDurationS: Long = 0L,                // 현재 셀에 머문 시간 (초) — HO timing 예측
    val hoCount30s: Int = 0,                     // 최근 30초 핸드오버 횟수 — 이동성 지표
    val rsrpDelta: Int? = null,                  // RSRP 변화량 (이전 샘플 대비, HO시 null)
    val sinrDelta: Int? = null,                  // SINR 변화량
    val cellInfoAgeMs: Long? = null,             // allCellInfo 데이터 나이 (ms) — stale 필터링
    // 핸드오버 직전 셀 정보 — A3 이벤트 분석용 (이전 RSRP vs 이웃셀 RSRP 비교)
    val prevServingCellId: String = "",
    val prevRsrp: Int? = null,
    val prevRsrq: Int? = null,

    val neighborsJson: String = "[]"         // RSRP 내림차순 정렬됨
) {
    fun toCsvRow(): String {
        val dt           = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date(timestamp))
        val rxMbps       = rxSpeedBps       * 8.0 / 1_000_000.0
        val txMbps       = txSpeedBps       * 8.0 / 1_000_000.0
        val mobileRxMbps = mobileRxSpeedBps * 8.0 / 1_000_000.0
        val mobileTxMbps = mobileTxSpeedBps * 8.0 / 1_000_000.0
        val escapedNbr   = neighborsJson.replace("\"", "\"\"")
        return buildString {
            append(timestamp);                            append(',')
            append(dt);                                   append(',')
            append(activity);                             append(',')
            append(latitude          ?: "");              append(',')
            append(longitude         ?: "");              append(',')
            append(gpsAccuracyM      ?: "");              append(',')
            append(gpsSpeedMs        ?: "");              append(',')
            append(gpsBearing        ?: "");              append(',')
            append(gpsAltitude       ?: "");              append(',')
            append(locationSource);                       append(',')
            append(locationAgeS      ?: "");              append(',')
            append(networkType);                          append(',')
            append(overrideNetworkType);                  append(',')
            append(generation);                           append(',')
            append(is5G);                                 append(',')
            append(is5GActual);                           append(',')
            append(is5GDisplay);                          append(',')
            append(nrCellSeen);                           append(',')
            append(nrServingCellSeen);                    append(',')
            append(servingCellId);                        append(',')
            append(servingPci        ?: "");              append(',')
            append(servingFreqArfcn  ?: "");              append(',')
            append(servingBandStr);                       append(',')
            append(servingTac        ?: "");              append(',')
            append(mcc);                                  append(',')
            append(mnc);                                  append(',')
            append(rsrp              ?: "");              append(',')
            append(rsrq              ?: "");              append(',')
            append(rssi              ?: "");              append(',')
            append(sinrSnr           ?: "");              append(',')
            append(signalLevel       ?: "");              append(',')
            append(timingAdvanceLte  ?: "");              append(',')
            append(csiRsrp           ?: "");              append(',')
            append(csiRsrq           ?: "");              append(',')
            append(csiSinr           ?: "");              append(',')
            append(rxSpeedBps);                           append(',')
            append(txSpeedBps);                           append(',')
            append("%.4f".format(rxMbps));                append(',')
            append("%.4f".format(txMbps));                append(',')
            append(mobileRxSpeedBps);                     append(',')
            append(mobileTxSpeedBps);                     append(',')
            append("%.4f".format(mobileRxMbps));          append(',')
            append("%.4f".format(mobileTxMbps));          append(',')
            append(wifiActive);                           append(',')
            append(neighborCount);                        append(',')
            append(nrNeighborCount);                      append(',')
            append(lteNeighborCount);                     append(',')
            append(bestNbrRsrp        ?: "");             append(',')
            append(bestNbrPci         ?: "");             append(',')
            append(bestNbrArfcn       ?: "");             append(',')
            append(imuSpeedMs         ?: "");             append(',')
            append(handoverDetected);                     append(',')
            append(pingPongDetected);                     append(',')
            append(collectTrigger);                       append(',')
            append(cellDurationS);                        append(',')
            append(hoCount30s);                           append(',')
            append(rsrpDelta          ?: "");             append(',')
            append(sinrDelta          ?: "");             append(',')
            append(cellInfoAgeMs      ?: "");             append(',')
            append(prevServingCellId);                    append(',')
            append(prevRsrp           ?: "");             append(',')
            append(prevRsrq           ?: "");             append(',')
            append('"'); append(escapedNbr); append('"')
        }
    }

    companion object {
        const val CSV_HEADER =
            "timestamp,datetime,activity," +
            "latitude,longitude,gps_accuracy_m,gps_speed_ms,gps_bearing_deg,gps_altitude_m," +
            "location_source,location_age_s," +
            "network_type,override_network_type,generation," +
            "is_5g,is_5g_actual,is_5g_display,nr_cell_seen,nr_serving_cell_seen," +
            "serving_cell_id,serving_pci,serving_freq_arfcn,serving_band,serving_tac,mcc,mnc," +
            "rsrp_dbm,rsrq_db,rssi_dbm,sinr_snr_db,signal_level," +
            "timing_advance_lte,csi_rsrp_dbm,csi_rsrq_db,csi_sinr_db," +
            "rx_speed_Bps,tx_speed_Bps,rx_bitrate_Mbps,tx_bitrate_Mbps," +
            "mobile_rx_speed_Bps,mobile_tx_speed_Bps,mobile_rx_bitrate_Mbps,mobile_tx_bitrate_Mbps," +
            "wifi_active," +
            "neighbor_count,nr_neighbor_count,lte_neighbor_count," +
            "best_nbr_rsrp_dbm,best_nbr_pci,best_nbr_arfcn," +
            "imu_speed_ms,handover_detected,ping_pong_detected," +
            "collect_trigger,cell_duration_s,ho_count_30s,rsrp_delta,sinr_delta,cell_info_age_ms," +
            "prev_serving_cell_id,prev_rsrp_dbm,prev_rsrq_db," +
            "neighbors_json"
    }
}
