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
    private lateinit var spinnerActivity: Spinner
    private lateinit var spinnerInterval: Spinner
    private lateinit var switchProbeDl: Switch
    private lateinit var etStation: EditText
    private lateinit var btnTagStation: Button

    private lateinit var previewCollector: NetworkDataCollector
    private lateinit var csvLogger: CsvLogger

    private val handler = Handler(Looper.getMainLooper())

    // 활동 태그 옵션 (표시명 → CSV 저장값)
    private val activityOptions = listOf(
        "선택 안 함" to "unknown",
        "도보"       to "walking",
        "지하철"     to "subway",
        "차량"       to "car",
        "실내/정지"  to "home",
        "기타"       to "other"
    )

    // 수집 주기 옵션 (표시명 → ms)
    private val intervalOptions = listOf(
        "2초"  to 2_000L,
        "5초"  to 5_000L,
        "10초" to 10_000L
    )

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

        btnStartStop    = findViewById(R.id.btn_start_stop)
        tvStatus        = findViewById(R.id.tv_status)
        tvLiveStats     = findViewById(R.id.tv_live_stats)
        tvFilePath      = findViewById(R.id.tv_file_path)
        lvFiles         = findViewById(R.id.lv_files)
        spinnerActivity = findViewById(R.id.spinner_activity)
        spinnerInterval = findViewById(R.id.spinner_interval)
        switchProbeDl   = findViewById(R.id.switch_probe_dl)
        etStation       = findViewById(R.id.et_station)
        btnTagStation   = findViewById(R.id.btn_tag_station)

        spinnerActivity.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            activityOptions.map { it.first }
        )
        spinnerInterval.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            intervalOptions.map { it.first }
        )
        spinnerInterval.setSelection(1)   // 기본 5초

        previewCollector = NetworkDataCollector(this)
        csvLogger        = CsvLogger(this)

        requestNeededPermissions()

        btnStartStop.setOnClickListener {
            if (NetworkLoggingService.isRunning) stopLogging() else startLogging()
        }

        lvFiles.setOnItemClickListener { _, _, pos, _ ->
            csvLogger.listFiles().getOrNull(pos)?.let { shareFile(it) }
        }

        // 지하 구간 수동 역 태그 — 이름 없이 눌러도 타임스탬프 앵커가 기록된다.
        // 이름을 넣으면 카카오 Local API로 좌표까지 붙는다 (승차/환승/하차역 정도만 권장).
        btnTagStation.setOnClickListener {
            val name = etStation.text.toString().trim()
            startService(Intent(this, NetworkLoggingService::class.java).apply {
                action = NetworkLoggingService.ACTION_TAG_STATION
                putExtra(NetworkLoggingService.EXTRA_STATION_NAME, name)
            })
            Toast.makeText(
                this,
                if (name.isEmpty()) "역 도착 앵커 기록 (이름 없음 — 분석 때 순번 매칭)"
                else "역 태그 기록: $name",
                Toast.LENGTH_SHORT
            ).show()
        }
    }

    override fun onResume() {
        super.onResume()
        previewCollector.startLocationUpdates()
        previewCollector.startTelephonyListener()
        previewCollector.startSensors()
        handler.post(uiTick)
        refreshFileList()
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(uiTick)
        previewCollector.stopLocationUpdates()
        previewCollector.stopTelephonyListener()
        previewCollector.stopSensors()
    }

    // ── UI 갱신 ───────────────────────────────────────────────────────────────

    private fun refreshUI() {
        val running = NetworkLoggingService.isRunning

        btnStartStop.text = if (running) "로깅 중지" else "로깅 시작"
        btnStartStop.setBackgroundColor(
            if (running) getColor(android.R.color.holo_red_dark)
            else         getColor(android.R.color.holo_green_dark)
        )
        spinnerActivity.isEnabled = !running  // 로깅 중 태그 변경 방지
        spinnerInterval.isEnabled = !running
        switchProbeDl.isEnabled   = !running
        btnTagStation.isEnabled   = running   // 역 태그는 로깅 중에만 의미 있음

        if (running) {
            tvStatus.text   = "● 로깅 중 | 기록 수: ${NetworkLoggingService.recordCount}"
            tvFilePath.text = "파일: ${NetworkLoggingService.activeFile?.name ?: "-"}"

            if (hasLocationPermission()) {
                runCatching {
                    val r = previewCollector.collect()
                    tvLiveStats.text = buildString {
                        appendLine("세대/종류  : ${r.generation} (${r.networkType})")
                        appendLine("오버라이드 : ${r.overrideNetworkType}")
                        appendLine("5G 감지   : SA=${r.is5GActual}  NSA=${r.is5GDisplay}  NR셀=${r.nrCellSeen}  서빙NR=${r.nrServingCellSeen}")

                        appendLine("위  도    : ${r.latitude?.let  { "%.6f".format(it) } ?: "취득 중..."}")
                        appendLine("경  도    : ${r.longitude?.let { "%.6f".format(it) } ?: "취득 중..."}")
                        appendLine("GPS정확도 : ${r.gpsAccuracyM?.let { "%.1f m".format(it) } ?: "-"}")
                        if (r.gpsSpeedMs != null)
                            appendLine("GPS속도   : ${"%.1f".format(r.gpsSpeedMs)} m/s  (${"%.1f".format(r.gpsSpeedMs * 3.6)} km/h)")
                        if (r.imuSpeedMs != null)
                            appendLine("IMU속도   : ${"%.1f".format(r.imuSpeedMs)} m/s  (${"%.1f".format(r.imuSpeedMs * 3.6)} km/h)")
                        if (r.gpsBearing != null)
                            appendLine("방  향    : ${"%.0f".format(r.gpsBearing)}°")
                        if (r.gpsAltitude != null)
                            appendLine("고  도    : ${"%.0f".format(r.gpsAltitude)} m")

                        appendLine("셀 ID     : ${r.servingCellId.ifEmpty { "-" }}")
                        if (r.servingPci != null)          appendLine("PCI       : ${r.servingPci}")
                        if (r.servingFreqArfcn != null)    appendLine("ARFCN     : ${r.servingFreqArfcn}")
                        if (r.servingBandStr.isNotEmpty()) appendLine("Band      : ${r.servingBandStr}")
                        if (r.servingTac != null)          appendLine("TAC       : ${r.servingTac}")
                        if (r.mcc.isNotEmpty())            appendLine("MCC/MNC   : ${r.mcc}/${r.mnc}")

                        appendLine("RSRP      : ${r.rsrp?.let    { "$it dBm" } ?: "-"}")
                        appendLine("RSRQ      : ${r.rsrq?.let    { "$it dB"  } ?: "-"}")
                        appendLine("RSSI      : ${r.rssi?.let    { "$it dBm" } ?: "-"}")
                        appendLine("SINR/SNR  : ${r.sinrSnr?.let { "$it dB"  } ?: "-"}")
                        appendLine("신호레벨  : ${r.signalLevel?.let { "$it / 4" } ?: "-"}")

                        if (r.timingAdvanceLte != null)
                            appendLine("TA(거리)  : ${r.timingAdvanceLte}  (~${"%.0f".format(r.timingAdvanceLte * 78.0)} m)")

                        if (r.csiRsrp != null) {
                            appendLine("CSI-RSRP  : ${r.csiRsrp} dBm")
                            appendLine("CSI-RSRQ  : ${r.csiRsrq?.let { "$it dB" } ?: "-"}")
                            appendLine("CSI-SINR  : ${r.csiSinr?.let { "$it dB" } ?: "-"}")
                        }

                        val rxMbps       = r.rxSpeedBps       * 8.0 / 1_000_000.0
                        val mobileRxMbps = r.mobileRxSpeedBps * 8.0 / 1_000_000.0
                        appendLine("수신(전체): ${formatBps(r.rxSpeedBps)}  (${"%.2f".format(rxMbps)} Mbps)${if (r.wifiActive) "  [Wi-Fi포함]" else ""}")
                        appendLine("수신(셀룰): ${formatBps(r.mobileRxSpeedBps)}  (${"%.2f".format(mobileRxMbps)} Mbps)")

                        if (r.bestNbrRsrp != null)
                            appendLine("최강이웃  : RSRP=${r.bestNbrRsrp}dBm  PCI=${r.bestNbrPci ?: "-"}  ARFCN=${r.bestNbrArfcn ?: "-"}")

                        if (r.handoverDetected)
                            appendLine(if (r.pingPongDetected) "⚠ 핑퐁 핸드오버! 이전=${r.prevServingCellId}  RSRP=${r.prevRsrp}dBm"
                                       else "→ 핸드오버  이전=${r.prevServingCellId}  RSRP=${r.prevRsrp}dBm")

                        appendLine("이웃기지국: 전체=${r.neighborCount}  NR=${r.nrNeighborCount}  LTE=${r.lteNeighborCount}")

                        if (r.pressureHpa != null)
                            appendLine("기  압    : ${"%.1f".format(r.pressureHpa)} hPa")
                        append("WiFi 스캔 : ${r.wifiApCount?.let { "AP ${it}개 (${r.wifiScanAgeS ?: "?"}초 전)" } ?: "-"}")
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
        val tag = activityOptions.getOrNull(spinnerActivity.selectedItemPosition)?.second ?: "unknown"
        val interval = intervalOptions.getOrNull(spinnerInterval.selectedItemPosition)?.second
            ?: NetworkLoggingService.DEFAULT_INTERVAL
        val intent = Intent(this, NetworkLoggingService::class.java).apply {
            putExtra(NetworkLoggingService.EXTRA_INTERVAL,     interval)
            putExtra(NetworkLoggingService.EXTRA_ACTIVITY_TAG, tag)
            putExtra(NetworkLoggingService.EXTRA_PROBE_DL,     switchProbeDl.isChecked)
        }
        startForegroundService(intent)
    }

    private fun stopLogging() {
        startService(Intent(this, NetworkLoggingService::class.java).apply {
            action = NetworkLoggingService.ACTION_STOP
        })
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
