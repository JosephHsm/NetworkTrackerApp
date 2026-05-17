package com.networktracker.data

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class NetworkRecord(
    val timestamp: Long = System.currentTimeMillis(),
    val latitude: Double? = null,
    val longitude: Double? = null,
    val gpsAccuracyM: Float? = null,
    val gpsSpeedMs: Float? = null,          // GPS 속도 (m/s) — 이동성 기반 핸드오버 예측에 필수
    val gpsBearing: Float? = null,           // GPS 방향 (도) — 이동 방향 기반 예측에 필수
    val gpsAltitude: Double? = null,         // GPS 고도 (m)

    val networkType: String = "UNKNOWN",     // 데이터 RAT: LTE, NR(5G) 등 (dataNetworkType 기준)
    val overrideNetworkType: String = "NONE",// 표시용: NONE, LTE_CA, NR_NSA, NR_ADVANCED
    val generation: String = "UNKNOWN",      // 2G / 3G / 4G / 5G
    val is5G: Boolean = false,               // actual | display | nrServingCellSeen 중 하나라도 true
    val is5GActual: Boolean = false,         // NR SA (NETWORK_TYPE_NR) 일 때만 true
    val is5GDisplay: Boolean = false,        // TelephonyDisplayInfo NR_NSA / NR_ADVANCED 일 때 true
    val nrCellSeen: Boolean = false,         // allCellInfo에서 CellInfoNr 존재 여부 (서빙+이웃 포함)
    val nrServingCellSeen: Boolean = false,  // allCellInfo에서 NR 셀이 직접 서빙(isRegistered=true)

    val servingCellId: String = "",
    val servingPci: Int? = null,             // Physical Cell ID (핸드오버 타겟 셀 식별)
    val servingFreqArfcn: Int? = null,       // EARFCN (LTE) 또는 NR-ARFCN (NR) — 채널 식별
    val servingBandStr: String = "",         // 주파수 밴드 (예: "1", "78") — API 31+ 에서만 수집
    val servingTac: Int? = null,             // Tracking Area Code — 핸드오버 구역 경계 감지
    val mcc: String = "",                    // Mobile Country Code (예: "450" = 한국)
    val mnc: String = "",                    // Mobile Network Code (예: "05" = SKT)

    val rsrp: Int? = null,                   // dBm  (LTE: RSRP / NR: SS-RSRP)
    val rsrq: Int? = null,                   // dB   (LTE: RSRQ / NR: SS-RSRQ)
    val rssi: Int? = null,                   // dBm  (LTE only)
    val sinrSnr: Int? = null,                // dB   (LTE: RS-SNR / NR: SS-SINR)
    val signalLevel: Int? = null,

    val timingAdvanceLte: Int? = null,       // LTE Timing Advance (0~1282; 1단위≈78m, 기지국 거리 추정)
    val csiRsrp: Int? = null,                // 5G CSI-RSRP (dBm) — 빔 관리 기반 채널 측정
    val csiRsrq: Int? = null,                // 5G CSI-RSRQ (dB)
    val csiSinr: Int? = null,                // 5G CSI-SINR (dB)

    val rxSpeedBps: Long = 0L,
    val txSpeedBps: Long = 0L,

    val neighborCount: Int = 0,
    val nrNeighborCount: Int = 0,            // NR(5G) 이웃 셀 수 — 5G 커버리지 밀도
    val lteNeighborCount: Int = 0,           // LTE 이웃 셀 수 — 4G↔5G 채널링 판단 근거

    val imuSpeedMs: Float? = null,           // 가속도 센서 적분 속도 (m/s) — GPS 불가 시 보조
    val handoverDetected: Boolean = false,   // 이전 샘플 대비 서빙셀 ID 변경(핸드오버 발생)
    val pingPongDetected: Boolean = false,   // 30초 내 이전 셀로 복귀(핑퐁 핸드오버)

    val neighborsJson: String = "[]"
) {
    fun toCsvRow(): String {
        val dt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date(timestamp))
        val escapedNeighbors = neighborsJson.replace("\"", "\"\"")
        val rxMbps = rxSpeedBps * 8.0 / 1_000_000.0
        val txMbps = txSpeedBps * 8.0 / 1_000_000.0
        return buildString {
            append(timestamp);                                append(',')
            append(dt);                                       append(',')
            append(latitude      ?: "");                      append(',')
            append(longitude     ?: "");                      append(',')
            append(gpsAccuracyM  ?: "");                      append(',')
            append(gpsSpeedMs    ?: "");                      append(',')
            append(gpsBearing    ?: "");                      append(',')
            append(gpsAltitude   ?: "");                      append(',')
            append(networkType);                              append(',')
            append(overrideNetworkType);                      append(',')
            append(generation);                               append(',')
            append(is5G);                                     append(',')
            append(is5GActual);                               append(',')
            append(is5GDisplay);                              append(',')
            append(nrCellSeen);                               append(',')
            append(nrServingCellSeen);                        append(',')
            append(servingCellId);                            append(',')
            append(servingPci        ?: "");                  append(',')
            append(servingFreqArfcn  ?: "");                  append(',')
            append(servingBandStr);                           append(',')
            append(servingTac        ?: "");                  append(',')
            append(mcc);                                      append(',')
            append(mnc);                                      append(',')
            append(rsrp              ?: "");                  append(',')
            append(rsrq              ?: "");                  append(',')
            append(rssi              ?: "");                  append(',')
            append(sinrSnr           ?: "");                  append(',')
            append(signalLevel       ?: "");                  append(',')
            append(timingAdvanceLte  ?: "");                  append(',')
            append(csiRsrp           ?: "");                  append(',')
            append(csiRsrq           ?: "");                  append(',')
            append(csiSinr           ?: "");                  append(',')
            append(rxSpeedBps);                               append(',')
            append(txSpeedBps);                               append(',')
            append("%.4f".format(rxMbps));                    append(',')
            append("%.4f".format(txMbps));                    append(',')
            append(neighborCount);                            append(',')
            append(nrNeighborCount);                          append(',')
            append(lteNeighborCount);                         append(',')
            append(imuSpeedMs        ?: "");                  append(',')
            append(handoverDetected);                         append(',')
            append(pingPongDetected);                         append(',')
            append('"'); append(escapedNeighbors); append('"')
        }
    }

    companion object {
        const val CSV_HEADER =
            "timestamp,datetime," +
            "latitude,longitude,gps_accuracy_m,gps_speed_ms,gps_bearing_deg,gps_altitude_m," +
            "network_type,override_network_type,generation," +
            "is_5g,is_5g_actual,is_5g_display,nr_cell_seen,nr_serving_cell_seen," +
            "serving_cell_id,serving_pci,serving_freq_arfcn,serving_band,serving_tac,mcc,mnc," +
            "rsrp_dbm,rsrq_db,rssi_dbm,sinr_snr_db,signal_level," +
            "timing_advance_lte,csi_rsrp_dbm,csi_rsrq_db,csi_sinr_db," +
            "rx_speed_Bps,tx_speed_Bps,rx_bitrate_Mbps,tx_bitrate_Mbps," +
            "neighbor_count,nr_neighbor_count,lte_neighbor_count," +
            "imu_speed_ms,handover_detected,ping_pong_detected," +
            "neighbors_json"
    }
}
