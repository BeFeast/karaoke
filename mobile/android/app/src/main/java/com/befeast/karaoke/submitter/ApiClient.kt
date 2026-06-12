// Thin OkHttp client for the karaoke coordinator: POST /jobs + GET /jobs.
// Maps server responses to user-facing messages. Never logs the token or any
// cookie value; ApiException messages carry only the server's value-free
// detail strings or plain status text.
package com.befeast.karaoke.submitter

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** User-facing API failure; the message is safe to render verbatim. */
class ApiException(message: String) : Exception(message)

class ApiClient(baseUrl: String, private val token: String) {

    data class Receipt(val id: Long, val title: String?, val status: String)

    data class JobRow(
        val id: Long,
        val title: String?,
        val sourceUrl: String,
        val status: String,
        val shareUrl: String,
    )

    private val base = baseUrl.trimEnd('/')

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private val jsonType = "application/json; charset=utf-8".toMediaType()

    fun submitJob(body: Map<String, String>): Receipt {
        val json = JSONObject()
        for ((key, value) in body) {
            json.put(key, value)
        }
        val request = Request.Builder()
            .url("$base/jobs")
            .header("Authorization", "Bearer $token")
            .post(json.toString().toRequestBody(jsonType))
            .build()
        execute(request).use { response ->
            when (response.code) {
                201 -> {
                    val out = JSONObject(response.body?.string().orEmpty())
                    return Receipt(
                        id = out.optLong("id"),
                        title = out.optString("title").takeIf { it.isNotEmpty() && it != "null" },
                        status = out.optString("status"),
                    )
                }
                401 -> throw ApiException("token rejected — re-check the pass in Settings")
                413 -> throw ApiException("cookie payload too large")
                422 -> throw ApiException("invalid URL or cookie file: ${detailOf(response)}")
                else -> throw ApiException("server error (HTTP ${response.code})")
            }
        }
    }

    fun recentJobs(limit: Int = 5): List<JobRow> {
        val request = Request.Builder()
            .url("$base/jobs?limit=$limit")
            .header("Authorization", "Bearer $token")
            .get()
            .build()
        execute(request).use { response ->
            when (response.code) {
                200 -> {
                    val rows = mutableListOf<JobRow>()
                    val array = JSONArray(response.body?.string().orEmpty())
                    for (i in 0 until array.length()) {
                        val job = array.getJSONObject(i)
                        rows.add(
                            JobRow(
                                id = job.optLong("id"),
                                title = job.optString("title")
                                    .takeIf { it.isNotEmpty() && it != "null" },
                                sourceUrl = job.optString("source_url"),
                                status = job.optString("status"),
                                shareUrl = job.optString("share_url"),
                            )
                        )
                    }
                    return rows
                }
                401 -> throw ApiException("token rejected — re-check the pass in Settings")
                else -> throw ApiException("server error (HTTP ${response.code})")
            }
        }
    }

    private fun execute(request: Request): Response =
        try {
            client.newCall(request).execute()
        } catch (_: IOException) {
            throw ApiException("network error — check the connection and retry")
        }

    /**
     * Extract a 422 detail. The server's own messages (HTTPException) are a
     * plain value-free string — surface verbatim. Pydantic validation errors
     * are a list of objects; surface only their `msg` fields, never `input`,
     * to keep the value-free discipline client-side too.
     */
    private fun detailOf(response: Response): String {
        val fallback = "request rejected by the server"
        val text = try {
            response.body?.string().orEmpty()
        } catch (_: IOException) {
            return fallback
        }
        return try {
            val detail = JSONObject(text).opt("detail")
            when (detail) {
                is String -> detail
                is JSONArray -> {
                    val messages = mutableListOf<String>()
                    for (i in 0 until detail.length()) {
                        val msg = detail.optJSONObject(i)?.optString("msg").orEmpty()
                        if (msg.isNotEmpty()) {
                            messages.add(msg)
                        }
                    }
                    if (messages.isEmpty()) fallback else messages.joinToString("; ")
                }
                else -> fallback
            }
        } catch (_: Exception) {
            fallback
        }
    }
}
