// Parser for a pasted Netscape cookies.txt blob — the manual-import fallback
// when Google blocks WebView sign-in (desktop-ecosystem equivalent: a
// "Get cookies.txt LOCALLY" export). Mirrors the server's
// `validate_netscape_cookies` (src/karaoke/api/cookies_store.py) so a jar
// accepted here is accepted there by construction.
//
// Security rule mirrored from the server: cookies are secrets. Errors carry
// only structural facts (line index, field count, flag names) — never the
// field contents.
//
// Pure Kotlin — zero android.* imports, JVM-unit-testable.
package com.befeast.karaoke.submitter.core

/** Value-free parse/validation failure, safe to show in the UI verbatim. */
class CookieParseException(message: String) : Exception(message)

object NetscapeJarParser {

    private const val FIELDS = 7
    private const val HTTPONLY_PREFIX = "#HttpOnly_"
    private val BOOLS = setOf("TRUE", "FALSE")

    /**
     * Parse [blob] as a Netscape cookies.txt. Returns the cookies on success;
     * throws [CookieParseException] (value-free message) when the blob is not
     * a usable jar — malformed lines, zero entries, or zero youtube.com
     * cookies (the server applies the same gate).
     */
    fun parse(blob: String): List<Cookie> {
        val cookies = mutableListOf<Cookie>()
        for ((index0, rawLine) in blob.lineSequence().withIndex()) {
            val index = index0 + 1
            val line = rawLine.trimEnd('\r')
            if (line.isBlank()) {
                continue
            }
            // Comments are skipped, except yt-dlp's `#HttpOnly_` data lines.
            val isHttpOnly = line.startsWith(HTTPONLY_PREFIX)
            if (line.startsWith("#") && !isHttpOnly) {
                continue
            }
            val dataLine = if (isHttpOnly) line.substring(HTTPONLY_PREFIX.length) else line
            val fields = dataLine.split("\t")
            if (fields.size != FIELDS) {
                throw CookieParseException(
                    "line $index: expected $FIELDS tab-separated fields, got ${fields.size}"
                )
            }
            val (domain, includeSub, path, secure, expiry) = fields
            val name = fields[5]
            val value = fields[6]
            if (domain.isBlank()) {
                throw CookieParseException("line $index: empty domain field")
            }
            if (includeSub.uppercase() !in BOOLS) {
                throw CookieParseException(
                    "line $index: include-subdomains flag must be TRUE/FALSE"
                )
            }
            if (secure.uppercase() !in BOOLS) {
                throw CookieParseException("line $index: secure flag must be TRUE/FALSE")
            }
            val expiryStr = expiry.trim()
            val expirySeconds: Long
            if (expiryStr.isEmpty()) {
                expirySeconds = 0
            } else {
                expirySeconds = expiryStr.toLongOrNull()
                    ?: throw CookieParseException(
                        "line $index: expiry must be an integer timestamp"
                    )
            }
            if (name.isBlank()) {
                throw CookieParseException("line $index: empty cookie name")
            }
            cookies.add(
                Cookie(
                    name = name,
                    value = value,
                    domain = domain,
                    path = path.ifEmpty { "/" },
                    secure = secure.uppercase() == "TRUE",
                    httpOnly = isHttpOnly,
                    hostOnly = includeSub.uppercase() == "FALSE",
                    session = expirySeconds <= 0,
                    expirationDate = if (expirySeconds > 0) expirySeconds else null,
                )
            )
        }
        if (cookies.isEmpty()) {
            throw CookieParseException("no cookie entries found")
        }
        if (countYoutubeCookies(cookies) == 0) {
            throw CookieParseException("no youtube.com cookies present")
        }
        return cookies
    }
}
