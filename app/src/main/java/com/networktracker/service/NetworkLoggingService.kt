package com.networktracker.service

import android.app.*
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import com.networktracker.collector.NetworkDataCollector
import com.networktracker.logger.CsvLogger
import com.networktracker.ui.MainActivity
import java.io.File

class NetworkLoggingService : Service() {

    companion object {
        const val ACTION_STOP        = "com.networktracker.STOP"
        const val EXTRA_INTERVAL     = "interval_ms"
        const val EXTRA_ACTIVITY_TAG = "activity_tag"
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
    private var intervalMs = DEFAULT_INTERVAL

    // 중복 수집 방지: 마지막 collect() 시각 추적 (timer tick과 핸드오버 콜백 동시 발화 대응)
    private var lastCollectMs = 0L

    private val tick = object : Runnable {
        override fun run() {
            lastCollectMs = System.currentTimeMillis()
            collector.collectTrigger = "periodic"
            val record = collector.collect()
            csvLogger.log(record)
            recordCount = csvLogger.recordCount()
            updateNotification()
            collector.refreshCellInfo()   // 다음 tick 전에 모뎀 셀 정보 갱신 요청
            handler.postDelayed(this, intervalMs)
        }
    }

    override fun onCreate() {
        super.onCreate()
        collector = NetworkDataCollector(this)
        csvLogger = CsvLogger(this)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { stopSelf(); return START_NOT_STICKY }

        intervalMs = intent?.getLongExtra(EXTRA_INTERVAL, DEFAULT_INTERVAL) ?: DEFAULT_INTERVAL
        val activityTag = intent?.getStringExtra(EXTRA_ACTIVITY_TAG) ?: ""

        collector.activityTag = activityTag
        activeFile  = csvLogger.startSession(activityTag)
        recordCount = 0

        startForeground(NOTIF_ID, buildNotification("로깅 시작..."))
        collector.startLocationUpdates()
        collector.startTelephonyListener()
        collector.startImuSensor()

        // 핸드오버 감지 시 즉시 추가 수집 (API 31+)
        // timer와 동시 발화 시 중복 방지: 마지막 수집으로부터 1초 미만이면 skip
        collector.onCellChangeDetected = {
            val now = System.currentTimeMillis()
            if (now - lastCollectMs >= 1_000L) {
                lastCollectMs = now
                collector.collectTrigger = "handover"
                val record = collector.collect()
                csvLogger.log(record)
                recordCount = csvLogger.recordCount()
                updateNotification()
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
        collector.stopLocationUpdates()
        collector.stopTelephonyListener()
        collector.stopImuSensor()
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
        mgr.notify(NOTIF_ID, buildNotification("수집 중: ${recordCount}개"))
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
