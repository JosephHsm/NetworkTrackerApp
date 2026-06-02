package com.networktracker.logger

import android.content.Context
import android.os.Environment
import com.networktracker.data.NetworkRecord
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 세션 시작 시 헤더를 파일에 즉시 기록하고, FLUSH_EVERY 레코드마다 append 저장한다.
 * 프로세스 강제 종료 시 최대 FLUSH_EVERY 개(약 2.5분)의 데이터만 유실된다.
 */
class CsvLogger(private val context: Context) {

    companion object {
        private const val FLUSH_EVERY = 30   // 30레코드(2.5분)마다 디스크에 flush
    }

    private val buffer = mutableListOf<NetworkRecord>()
    private var sessionFile: File? = null
    private var totalCount = 0

    /** activityTag 는 파일명과 activity 컬럼에 함께 사용된다. */
    fun startSession(activityTag: String = ""): File {
        buffer.clear()
        totalCount = 0

        val dir = context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS)
            ?: context.filesDir
        dir.mkdirs()

        val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
        val tag   = if (activityTag.isNotEmpty()) "_$activityTag" else ""
        val file  = File(dir, "network_log_$stamp$tag.csv")
        sessionFile = file

        // 헤더를 즉시 기록 — 세션 시작과 동시에 파일이 유효한 상태가 됨
        runCatching { FileWriter(file, false).use { it.appendLine(NetworkRecord.CSV_HEADER) } }
        return file
    }

    fun log(record: NetworkRecord) {
        buffer.add(record)
        totalCount++
        if (buffer.size >= FLUSH_EVERY) flush()
    }

    fun recordCount(): Int = totalCount

    fun getActiveFile(): File? = sessionFile

    /** 버퍼를 파일에 append하고 비운다. */
    private fun flush() {
        val file = sessionFile ?: return
        if (buffer.isEmpty()) return
        runCatching {
            FileWriter(file, true).use { w -> buffer.forEach { w.appendLine(it.toCsvRow()) } }
        }
        buffer.clear()
    }

    /** 남은 버퍼를 flush하고 세션을 닫는다. */
    fun saveAndClose(): File? {
        flush()
        val file = sessionFile
        sessionFile = null
        return file
    }

    fun listFiles(): List<File> {
        val dir = context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS) ?: return emptyList()
        return dir.listFiles { f -> f.name.startsWith("network_log_") && f.name.endsWith(".csv") }
            ?.sortedByDescending { it.lastModified() } ?: emptyList()
    }
}
