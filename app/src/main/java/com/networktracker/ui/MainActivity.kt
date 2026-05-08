package com.networktracker.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import com.networktracker.R
import com.networktracker.collector.NetworkDataCollector
import com.networktracker.logger.CsvLogger
import com.networktracker.service.NetworkLoggingService

class MainActivity : AppCompatActivity() {

    private lateinit var btnStartStop: Button
    private lateinit var tvStatus: TextView
    private lateinit var tvLiveStats: TextView
    private lateinit var tvFilePath: TextView
    private lateinit var lvFiles: ListView

    private lateinit var previewCollector: NetworkDataCollector
    private lateinit var csvLogger: CsvLogger

    private val handler = Handler(Looper.getMainLooper())

    private val uiTick = object : Runnable {
        override fun run() {
            refreshUI()
            handler.postDelayed(this, 1000)
        }
    }

    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        val denied = grants.filterValues { !it }.keys
        if (denied.isNotEmpty())
            Toast.makeText(this, "거부된 권한이 있습니다. 설정에서 허용해 주세요.", Toast.LENGTH_LONG).show()
    }

    // ── 생명주기 ──────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        btnStartStop = findViewById(R.id.btn_start_stop)
        tvStatus     = findViewById(R.id.tv_status)
        tvLiveStats  = findViewById(R.id.tv_live_stats)
        tvFilePath   = findViewById(R.id.tv_file_path)
        lvFiles      = findViewById(R.id.lv_files)

        previewCollector = NetworkDataCollector(this)
        csvLogger        = CsvLogger(this)

        requestNeededPermissions()

        btnStartStop.setOnClickListener {
            if (NetworkLoggingService.isRunning) stopLogging() else startLogging()
        }

        lvFiles.setOnItemClickListener { _, _, pos, _ ->
            csvLogger.listFiles().getOrNull(pos)?.let { shareFile(it) }
        }
    }

    override fun onResume() {
        super.onResume()
        // 미리보기 UI용 별도 리스너 시작 (5G NSA 감지 포함)
        previewCollector.startLocationUpdates()
        previewCollector.startTelephonyListener()
        handler.post(uiTick)
        refreshFileList()
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(uiTick)
        previewCollector.stopLocationUpdates()
        previewCollector.stopTelephonyListener()
    }

    // ── UI 갱신 ───────────────────────────────────────────────────────────────

    private fun refreshUI() {
        val running = NetworkLoggingService.isRunning

        btnStartStop.text = if (running) "로깅 중지" else "로깅 시작"
        btnStartStop.setBackgroundColor(
            if (running) getColor(android.R.color.holo_red_dark)
            else         getColor(android.R.color.holo_green_dark)
        )

        if (running) {
            tvStatus.text   = "● 로깅 중 | 기록 수: ${NetworkLoggingService.recordCount}"
            tvFilePath.text = "파일: ${NetworkLoggingService.activeFile?.name ?: "-"}"

            if (hasLocationPermission()) {
                runCatching {
                    val r = previewCollector.collect()
                    tvLiveStats.text = buildString {
                        // 세대/망 정보
                        appendLine("세대/종류  : ${r.generation} (${r.networkType})")
                        appendLine("오버라이드 : ${r.overrideNetworkType}")
                        appendLine("5G 감지   : SA=${r.is5GActual}  NSA=${r.is5GDisplay}  NR셀=${r.nrCellSeen}  서빙NR=${r.nrServingCellSeen}")

                        // 위치
                        appendLine("위  도    : ${r.latitude?.let  { "%.6f".format(it) } ?: "취득 중..."}")
                        appendLine("경  도    : ${r.longitude?.let { "%.6f".format(it) } ?: "취득 중..."}")
                        appendLine("GPS정확도 : ${r.gpsAccuracyM?.let { "%.1f m".format(it) } ?: "-"}")
                        if (r.gpsSpeedMs != null)
                            appendLine("속  도    : ${"%.1f".format(r.gpsSpeedMs)} m/s  (${"%.1f".format(r.gpsSpeedMs * 3.6)} km/h)")
                        if (r.gpsBearing != null)
                            appendLine("방  향    : ${"%.0f".format(r.gpsBearing)}°")
                        if (r.gpsAltitude != null)
                            appendLine("고  도    : ${"%.0f".format(r.gpsAltitude)} m")

                        // 셀 식별
                        appendLine("셀 ID     : ${r.servingCellId.ifEmpty { "-" }}")
                        if (r.servingPci != null)       appendLine("PCI       : ${r.servingPci}")
                        if (r.servingFreqArfcn != null) appendLine("ARFCN     : ${r.servingFreqArfcn}")
                        if (r.servingBandStr.isNotEmpty()) appendLine("Band      : ${r.servingBandStr}")
                        if (r.servingTac != null)       appendLine("TAC       : ${r.servingTac}")
                        if (r.mcc.isNotEmpty())         appendLine("MCC/MNC   : ${r.mcc}/${r.mnc}")

                        // LTE/NR 공통 신호
                        appendLine("RSRP      : ${r.rsrp?.let    { "$it dBm" } ?: "-"}")
                        appendLine("RSRQ      : ${r.rsrq?.let    { "$it dB"  } ?: "-"}")
                        appendLine("RSSI      : ${r.rssi?.let    { "$it dBm" } ?: "-"}")
                        appendLine("SINR/SNR  : ${r.sinrSnr?.let { "$it dB"  } ?: "-"}")
                        appendLine("신호레벨  : ${r.signalLevel?.let { "$it / 4" } ?: "-"}")

                        // LTE 전용
                        if (r.timingAdvanceLte != null)
                            appendLine("TA(거리)  : ${r.timingAdvanceLte}  (~${"%.0f".format(r.timingAdvanceLte * 78.0)} m)")

                        // 5G CSI 측정값
                        if (r.csiRsrp != null) {
                            appendLine("CSI-RSRP  : ${r.csiRsrp} dBm")
                            appendLine("CSI-RSRQ  : ${r.csiRsrq?.let { "$it dB" } ?: "-"}")
                            appendLine("CSI-SINR  : ${r.csiSinr?.let { "$it dB" } ?: "-"}")
                        }

                        // 처리량
                        val rxMbps = r.rxSpeedBps * 8.0 / 1_000_000.0
                        val txMbps = r.txSpeedBps * 8.0 / 1_000_000.0
                        appendLine("수신속도  : ${formatBps(r.rxSpeedBps)}  (${"%.2f".format(rxMbps)} Mbps)")
                        appendLine("송신속도  : ${formatBps(r.txSpeedBps)}  (${"%.2f".format(txMbps)} Mbps)")

                        // 이웃 셀
                        append("이웃기지국: 전체=${r.neighborCount}  NR=${r.nrNeighborCount}  LTE=${r.lteNeighborCount}")
                    }
                }
            }
        } else {
            tvStatus.text    = "○ 대기 중"
            tvFilePath.text  = ""
            tvLiveStats.text = ""
        }
    }

    private fun refreshFileList() {
        val files = csvLogger.listFiles()
        lvFiles.adapter = ArrayAdapter(
            this, android.R.layout.simple_list_item_1,
            files.map { "${it.name}  (${formatBytes(it.length())})" }
        )
    }

    // ── 서비스 제어 ────────────────────────────────────────────────────────────

    private fun startLogging() {
        if (!hasLocationPermission() || !hasPhoneStatePermission()) {
            requestNeededPermissions(); return
        }
        val intent = Intent(this, NetworkLoggingService::class.java).apply {
            putExtra(NetworkLoggingService.EXTRA_INTERVAL, 5_000L)
        }
        startForegroundService(intent)
    }

    private fun stopLogging() {
        startService(
            Intent(this, NetworkLoggingService::class.java).apply {
                action = NetworkLoggingService.ACTION_STOP
            }
        )
        handler.postDelayed({ refreshFileList() }, 2000)
    }

    // ── 파일 공유 ─────────────────────────────────────────────────────────────

    private fun shareFile(file: java.io.File) {
        runCatching {
            val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
            startActivity(Intent.createChooser(
                Intent(Intent.ACTION_SEND).apply {
                    type = "text/csv"
                    putExtra(Intent.EXTRA_STREAM, uri)
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }, "CSV 파일 공유"
            ))
        }.onFailure {
            Toast.makeText(this, "공유 실패: ${it.message}", Toast.LENGTH_SHORT).show()
        }
    }

    // ── 권한 ──────────────────────────────────────────────────────────────────

    private fun requestNeededPermissions() {
        val needed = mutableListOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.READ_PHONE_STATE
        ).apply {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                add(Manifest.permission.POST_NOTIFICATIONS)
        }.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (needed.isNotEmpty()) permLauncher.launch(needed.toTypedArray())
    }

    private fun hasLocationPermission() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED

    private fun hasPhoneStatePermission() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.READ_PHONE_STATE) ==
                PackageManager.PERMISSION_GRANTED

    // ── 포맷 헬퍼 ─────────────────────────────────────────────────────────────

    private fun formatBps(bps: Long) = when {
        bps >= 1_000_000 -> "%.1f MB/s".format(bps / 1_000_000.0)
        bps >= 1_000     -> "%.1f KB/s".format(bps / 1_000.0)
        else             -> "$bps B/s"
    }

    private fun formatBytes(b: Long) = when {
        b >= 1_048_576 -> "%.1f MB".format(b / 1_048_576.0)
        b >= 1_024     -> "%.1f KB".format(b / 1_024.0)
        else           -> "$b B"
    }
}
