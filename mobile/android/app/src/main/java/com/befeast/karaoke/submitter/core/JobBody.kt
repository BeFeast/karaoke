// Request-body builder for `POST /jobs` — pure Kotlin mirror of
// extension/chrome/cookies.js `buildJobBody`, returned as an ordered map so
// the gate (`youtube_cookies` attached only when a youtube.com cookie exists)
// is JVM-unit-testable without any JSON library.
package com.befeast.karaoke.submitter.core

fun buildJobBody(
    url: String,
    source: String? = null,
    title: String? = null,
    cookies: List<Cookie> = emptyList(),
): LinkedHashMap<String, String> {
    val body = LinkedHashMap<String, String>()
    body["url"] = url
    if (!source.isNullOrEmpty()) {
        body["source"] = source
    }
    if (!title.isNullOrEmpty()) {
        body["title"] = title
    }
    // Attach the jar ONLY when the user is actually signed in to YouTube —
    // a jar of stray google.com cookies alone is useless to yt-dlp and the
    // server would reject it (no youtube.com cookies present).
    if (countYoutubeCookies(cookies) > 0) {
        body["youtube_cookies"] = serializeNetscapeCookies(cookies)
    }
    return body
}
