package com.networktracker.collector

import android.os.Handler
import android.os.Looper
import com.networktracker.BuildConfig
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import kotlin.concurrent.thread

/**
 * 카카오 Local API(키워드 검색)로 지하철역 이름 → 좌표를 조회한다.
 *
 * 용도: 지하 구간 수동 역 태그 앵커.
 * 카카오맵 API는 측위(내 위치 추정) 기능이 없으므로 GPS 대체는 불가능하다.
 * 대신 사용자가 "지금 OO역"이라고 태그하면 그 역의 좌표를 받아와
 * ground-truth 앵커(collect_trigger="anchor" 행)로 기록하고,
 * 사후 맵매칭(CHANGES_GPS_IMPROVEMENT.md §6)의 보간 기준점으로 쓴다.
 *
 * API 키: local.properties의 kakao.rest.api.key → BuildConfig.KAKAO_REST_API_KEY.
 * 키가 비어 있거나 조회 실패 시에도 앵커는 역명만으로 기록된다(좌표 공백).
 */
object KakaoStationResolver {

    private const val ENDPOINT = "https://dapi.kakao.com/v2/local/search/keyword.json"
    private const val CATEGORY_SUBWAY = "SW8"   // 지하철역 카테고리
    private const val TIMEOUT_MS = 4_000

    data class StationResult(val name: String, val lat: Double?, val lon: Double?)

    private val mainHandler = Handler(Looper.getMainLooper())

    /**
     * 비동기 조회. callback은 메인 스레드에서 호출된다.
     * 실패해도 반드시 callback이 불린다 (lat/lon = null).
     */
    fun resolve(stationName: String, callback: (StationResult) -> Unit) {
        val key = BuildConfig.KAKAO_REST_API_KEY
        if (key.isBlank()) {
            callback(StationResult(stationName, null, null))
            return
        }
        thread(name = "KakaoResolver") {
            val result = runCatching {
                // "강남" → "강남역" 형태로 검색 (이미 "역"으로 끝나면 그대로)
                val query = if (stationName.endsWith("역")) stationName else "${stationName}역"
                val url = URL(
                    "$ENDPOINT?query=${URLEncoder.encode(query, "UTF-8")}" +
                    "&category_group_code=$CATEGORY_SUBWAY&size=1"
                )
                val conn = url.openConnection() as HttpURLConnection
                conn.connectTimeout = TIMEOUT_MS
                conn.readTimeout = TIMEOUT_MS
                conn.setRequestProperty("Authorization", "KakaoAK $key")
                try {
                    val body = conn.inputStream.bufferedReader().use { it.readText() }
                    val docs = JSONObject(body).getJSONArray("documents")
                    if (docs.length() > 0) {
                        val doc = docs.getJSONObject(0)
                        StationResult(
                            name = doc.optString("place_name", stationName),
                            lat  = doc.getString("y").toDouble(),
                            lon  = doc.getString("x").toDouble()
                        )
                    } else StationResult(stationName, null, null)
                } finally {
                    conn.disconnect()
                }
            }.getOrElse { StationResult(stationName, null, null) }
            mainHandler.post { callback(result) }
        }
    }
}
