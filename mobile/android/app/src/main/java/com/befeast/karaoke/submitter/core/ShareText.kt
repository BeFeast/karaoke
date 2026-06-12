// Extracts the submit URL from ACTION_SEND share text. The YouTube app shares
// links as text and sometimes prepends the title ("Watch \"…\" on YouTube\n
// https://youtu.be/…"), so take the FIRST http(s):// token. Hosts are not
// restricted — the server accepts any yt-dlp-supported URL; the only
// requirement is a parseable absolute http(s) URL.
//
// Pure Kotlin (java.net.URI only) — JVM-unit-testable.
package com.befeast.karaoke.submitter.core

import java.net.URI

object ShareText {

    /** First parseable absolute http(s) URL token in [text], or null. */
    fun extractFirstUrl(text: String?): String? {
        if (text.isNullOrBlank()) {
            return null
        }
        for (token in text.split(Regex("\\s+"))) {
            val candidate = token.trim()
            val lower = candidate.lowercase()
            if (!lower.startsWith("http://") && !lower.startsWith("https://")) {
                continue
            }
            val uri = try {
                URI(candidate)
            } catch (_: Exception) {
                continue
            }
            if (uri.host.isNullOrEmpty()) {
                continue
            }
            return candidate
        }
        return null
    }
}
