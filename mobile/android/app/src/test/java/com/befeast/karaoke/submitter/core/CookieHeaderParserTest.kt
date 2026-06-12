package com.befeast.karaoke.submitter.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * CookieManager.getCookie() header-string parsing + per-domain attribute
 * synthesis + (domain, name) dedupe.
 */
class CookieHeaderParserTest {

    @Test
    fun `parses a header string into name-value pairs`() {
        val pairs = CookieHeaderParser.parseHeader("SID=abc; HSID=def")
        assertEquals(listOf("SID" to "abc", "HSID" to "def"), pairs)
    }

    @Test
    fun `values may contain equals signs`() {
        val pairs = CookieHeaderParser.parseHeader("PREF=f6=400&tz=UTC")
        assertEquals(listOf("PREF" to "f6=400&tz=UTC"), pairs)
    }

    @Test
    fun `null, blank, and degenerate headers parse to nothing`() {
        assertTrue(CookieHeaderParser.parseHeader(null).isEmpty())
        assertTrue(CookieHeaderParser.parseHeader("  ").isEmpty())
        assertTrue(CookieHeaderParser.parseHeader(";;;").isEmpty())
        assertTrue(CookieHeaderParser.parseHeader("=orphanvalue").isEmpty())
    }

    @Test
    fun `synthesizes Netscape attributes per registrable domain`() {
        val cookies = CookieHeaderParser.synthesize(listOf("youtube.com" to "SID=abc"))
        assertEquals(1, cookies.size)
        val cookie = cookies[0]
        assertEquals(".youtube.com", cookie.domain)
        assertEquals("/", cookie.path)
        assertTrue(cookie.secure)
        assertFalse(cookie.httpOnly)
        assertFalse(cookie.hostOnly)
        assertTrue(cookie.session)
        // Full line: include-subdomains TRUE, secure TRUE, session expiry 0,
        // and no #HttpOnly_ prefix (httpOnly is unknowable via getCookie()).
        assertEquals(
            ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc",
            cookieToNetscapeLine(cookie),
        )
    }

    @Test
    fun `dedupes by domain and name keeping the first occurrence`() {
        val cookies = CookieHeaderParser.synthesize(
            listOf(
                "youtube.com" to "SID=first; PREF=tz=UTC",
                "youtube.com" to "SID=second",
                "google.com" to "SID=google-scoped",
                "google.com" to null,
            )
        )
        assertEquals(3, cookies.size)
        assertEquals("first", cookies.first { it.domain == ".youtube.com" && it.name == "SID" }.value)
        assertEquals("tz=UTC", cookies.first { it.name == "PREF" }.value)
        // Same name under a different registrable domain is NOT a duplicate.
        assertEquals("google-scoped", cookies.first { it.domain == ".google.com" }.value)
    }

    @Test
    fun `synthesized youtube cookies pass the attach gate`() {
        val cookies = CookieHeaderParser.synthesize(
            listOf("youtube.com" to "SID=abc", "google.com" to "NID=xyz")
        )
        assertEquals(1, countYoutubeCookies(cookies))
        val body = buildJobBody("https://youtu.be/abc", cookies = cookies)
        assertTrue(body.containsKey("youtube_cookies"))
    }

    @Test
    fun `google-only webview cookies do not attach a jar`() {
        val cookies = CookieHeaderParser.synthesize(listOf("google.com" to "NID=xyz"))
        assertEquals(0, countYoutubeCookies(cookies))
        assertFalse(
            buildJobBody("https://youtu.be/abc", cookies = cookies)
                .containsKey("youtube_cookies")
        )
    }

    @Test
    fun `query list covers the five sign-in origins`() {
        assertEquals(
            listOf(
                "https://www.youtube.com",
                "https://youtube.com",
                "https://google.com",
                "https://www.google.com",
                "https://accounts.google.com",
            ),
            CookieHeaderParser.QUERY_URLS.map { it.first },
        )
        assertEquals(
            setOf("youtube.com", "google.com"),
            CookieHeaderParser.QUERY_URLS.map { it.second }.toSet(),
        )
    }
}
