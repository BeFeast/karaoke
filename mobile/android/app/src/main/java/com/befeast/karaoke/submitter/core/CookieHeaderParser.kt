// Turns Android `CookieManager.getCookie(url)` header strings into [Cookie]s.
//
// `getCookie()` returns only `name=value; name2=value2` — no domain/path/
// secure/expiry/httpOnly metadata (it DOES include httpOnly cookies, unlike
// document.cookie). Attributes are therefore synthesized per queried
// registrable domain: `.youtube.com` / `.google.com`, include-subdomains TRUE,
// path `/`, secure TRUE, expiry 0 (session — yt-dlp treats 0 as session, fine
// for a one-shot per-job jar), and NO `#HttpOnly_` prefix (httpOnly status is
// unknowable via this API; the prefix is browser round-trip metadata only —
// yt-dlp and the server's validator both accept plain data lines).
//
// Pure Kotlin — zero android.* imports; the Android side feeds it the header
// strings (see WebViewCookies).
package com.befeast.karaoke.submitter.core

object CookieHeaderParser {

    /** URLs queried against CookieManager, paired with their registrable domain. */
    val QUERY_URLS: List<Pair<String, String>> = listOf(
        "https://www.youtube.com" to "youtube.com",
        "https://youtube.com" to "youtube.com",
        "https://google.com" to "google.com",
        "https://www.google.com" to "google.com",
        "https://accounts.google.com" to "google.com",
    )

    /**
     * Parse one `Cookie:`-style header string into (name, value) pairs.
     * Values may themselves contain `=` (e.g. `PREF=f6=400&tz=UTC`), so each
     * `;`-separated token splits at the FIRST `=` only.
     */
    fun parseHeader(header: String?): List<Pair<String, String>> {
        if (header.isNullOrBlank()) {
            return emptyList()
        }
        val pairs = mutableListOf<Pair<String, String>>()
        for (token in header.split(";")) {
            val trimmed = token.trim()
            val eq = trimmed.indexOf('=')
            if (eq <= 0) {
                continue
            }
            val name = trimmed.substring(0, eq).trim()
            val value = trimmed.substring(eq + 1)
            if (name.isEmpty()) {
                continue
            }
            pairs.add(name to value)
        }
        return pairs
    }

    /**
     * Synthesize [Cookie]s from `(registrableDomain, headerString)` pairs in
     * query order, deduped by `(domain, name)` keeping the first occurrence.
     */
    fun synthesize(headersByDomain: List<Pair<String, String?>>): List<Cookie> {
        val seen = mutableSetOf<Pair<String, String>>()
        val cookies = mutableListOf<Cookie>()
        for ((registrable, header) in headersByDomain) {
            val domain = ".$registrable"
            for ((name, value) in parseHeader(header)) {
                if (!seen.add(domain to name)) {
                    continue
                }
                cookies.add(
                    Cookie(
                        name = name,
                        value = value,
                        domain = domain,
                        path = "/",
                        secure = true,
                        httpOnly = false,
                        hostOnly = false,
                        session = true,
                        expirationDate = null,
                    )
                )
            }
        }
        return cookies
    }
}
