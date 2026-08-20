package com.networktracker.service

import android.app.*
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import com.networktracker.collector.ActiveProbe
import com.networktracker.collector.KakaoStationResolver
import com.networktracker.collector.NetworkDataCollector
import com.networktracker.logger.CsvLogger
import com.networktracker.ui.MainActivity
import java.io.File

class NetworkLoggingService : Service() {

    companion object {
        const val ACTION_STOP        = "com.networktracker.STOP"
        const val ACTION_TAG_STATION = "com.networktracker.TAG_STATION"
        const val EXTRA_INTERVAL     = "interval_ms"
        const val EXTRA_ACTIVITY_TAG = "activity_tag"
        const val EXTRA_PROBE_DL     = "probe_dl_enabled"
        const val EXTRA_STATION_NAME = "station_name"
        const val DEFAULT_INTERVAL   = 5_000L
        private const val CHANNEL_ID = "nt_channel"
        private const val NOTIF_ID   = 1001

        @Volatile var isRunning   = false
        @Volatile var recordCount = 0
        @Volatile var activeFile: File? = null
    }

    private val handler = Handler(Looper.getMainLooper())
    private lateinit var collector: NetworkDataCollector
    private lateinit var csvLogger: CsvLogger
    private lateinit var probe: ActiveProbe
    private var intervalMs = DEFAULT_INTERVAL

    // 중복 수집 방지: 마지막 collect() 시각 추적 (timer tick과 핸드오버 콜백 동시 발화 대응)
    private var lastCollectMs = 0L
    // 익명 앵커 순번 — 세션 내 "stop_1", "stop_2", ... 로 기록
    private var anchorSeq = 0

    /** 프로브 결과를 collector에 주입하고 수집·기록한다. 모든 수집 경로가 이 함수를 거친다. */
    private fun doCollect(trigger: String) {
        collector.collectTrigger = trigger
        collector.externalRttMs  = probe.lastRttMs
        probe.consumeDlResult()?.let { collector.externalDlMbps = it }
        val record = collector.collect()
        csvLogger.log(record)
        recordCount = csvLogger.recordCount()
        updateNotification()
    }

    private val tick = object : Runnable {
        override fun run() {
            lastCollectMs = System.currentTimeMillis()
            doCollect("periodic")
            collector.refreshCellInfo()   // 다음 tick 전에 모뎀 셀 정보 갱신 요청
            handler.postDelayed(this, intervalMs)
        }
    }

    override fun onCreate() {
        super.onCreate()
        collector = NetworkDataCollector(this)
        csvLogger = CsvLogger(this)
        probe     = ActiveProbe(this)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { stopSelf(); return START_NOT_STICKY }
            ACTION_TAG_STATION -> {
                // 지하 구간 수동 역 태그.
                // 이름 있음 → 카카오 Local API로 좌표 조회 후 anchor 행 기록.
                // 이름 없음 → 즉시 타임스탬프만 있는 익명 앵커 기록 (역명은 분석 때 순번 매칭).
                val name = intent.getStringExtra(EXTRA_STATION_NAME)?.trim().orEmpty()
                if (isRunning) {
                    if (name.isEmpty()) {
                        anchorSeq++
                        collector.pendingAnchor =
                            KakaoStationResolver.StationResult("stop_$anchorSeq", null, null)
                        lastCollectMs = System.currentTimeMillis()
                        doCollect("anchor")
                    } else {
                        KakaoStationResolver.resolve(name) { result ->
                            if (isRunning) {
                                collector.pendingAnchor = result
                                lastCollectMs = System.currentTimeMillis()
                                doCollect("anchor")
                            }
                        }
                    }
                }
                return START_STICKY
            }
        }

        intervalMs = intent?.getLongExtra(EXTRA_INTERVAL, DEFAULT_INTERVAL) ?: DEFAULT_INTERVAL
        val activityTag    = intent?.getStringExtra(EXTRA_ACTIVITY_TAG) ?: ""
        val probeDlEnabled = intent?.getBooleanExtra(EXTRA_PROBE_DL, false) ?: false

        collector.activityTag = activityTag
        activeFile  = csvLogger.startSession(activityTag)
        recordCount = 0
        anchorSeq   = 0

        // Android 14+: 위치형 FGS는 위치 권한이 없으면 startForeground가 SecurityException을 던진다.
        // 앱이 죽지 않도록 잡아서 서비스를 정상 종료한다.
        try {
            startForeground(NOTIF_ID, buildNotification("로깅 시작..."))
        } catch (e: Exception) {
            stopSelf()
            return START_NOT_STICKY
        }
        collector.startLocationUpdates()
        collector.startTelephonyListener()
        collector.startSensors()
        // RTT는 항상 측정(무시 가능한 트래픽), 다운로드 버스트는 토글로 결정
        probe.start(intervalMs, probeDlEnabled)

        // 핸드오버 감지 시 즉시 추가 수집 (API 31+)
        // timer와 동시 발화 시 중복 방지: 마지막 수집으로부터 1초 미만이면 skip
        collector.onCellChangeDetected = {
            val now = System.currentTimeMillis()
            if (now - lastCollectMs >= 1_000L) {
                lastCollectMs = now
                doCollect("handover")
            }
        }

        handler.post(tick)
        isRunning = true
        return START_STICKY
    }

    override fun onDestroy() {
        isRunning = false
        handler.removeCallbacks(tick)
        collector.onCellChangeDetected = null
        probe.stop()
        collector.stopLocationUpdates()
        collector.stopTelephonyListener()
        collector.stopSensors()
        activeFile = csvLogger.saveAndClose()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun buildNotification(text: String): Notification {
        val openIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stopIntent = PendingIntent.getService(
            this, 1,
            Intent(this, NetworkLoggingService::class.java).apply { action = ACTION_STOP },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("네트워크 트래커")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentIntent(openIntent)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "중지", stopIntent)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification() {
        val mgr = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val probeInfo = if (probe.probeBytesUsed > 0)
            " | 프로브 ${probe.probeBytesUsed / 1_048_576}MB" else ""
        mgr.notify(NOTIF_ID, buildNotification("수집 중: ${recordCount}개$probeInfo"))
    }

    private fun createNotificationChannel() {
        val ch = NotificationChannel(CHANNEL_ID, "네트워크 트래커",
            NotificationManager.IMPORTANCE_LOW).apply {
            description = "네트워크 데이터 로깅 서비스"
            setShowBadge(false)
        }
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(ch)
    }
}
