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
 * 로깅 시작 시 세션 파일 경로만 확보하고 레코드를 메모리에 누적한다.
 * 로깅 중지(saveAndClose) 시 헤더 + 전체 데이터를 한 번에 파일로 기록한다.
 * 파일 위치: Android/data/com.networktracker/files/Documents/
 */
class CsvLogger(private val context: Context) {

    private val records = mutableListOf<NetworkRecord>()
    private var sessionFile: File? = null

    fun startSession(): File {
        records.clear()

        val dir = context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS)
            ?: context.filesDir
        dir.mkdirs()

        val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
        val file  = File(dir, "network_log_$stamp.csv")
        sessionFile = file
        return file
    }

    fun log(record: NetworkRecord) {
        records.add(record)
    }

    /** 누적된 레코드를 CSV 파일로 한번에 저장하고 파일을 반환한다. */
    fun saveAndClose(): File? {
        val file = sessionFile ?: return null
        runCatching {
            FileWriter(file, false).use { w ->
                w.appendLine(NetworkRecord.CSV_HEADER)
                records.forEach { w.appendLine(it.toCsvRow()) }
            }
        }
        records.clear()
        sessionFile = null
        return file
    }

    fun recordCount(): Int = records.size

    fun getActiveFile(): File? = sessionFile

    fun listFiles(): List<File> {
        val dir = context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS) ?: return emptyList()
        return dir.listFiles { f -> f.name.startsWith("network_log_") && f.name.endsWith(".csv") }
            ?.sortedByDescending { it.lastModified() } ?: emptyList()
    }
}
