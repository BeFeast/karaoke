package com.befeast.karaoke.submitter.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** Manual cookies.txt paste fallback: parse + validate + round-trip. */
class NetscapeJarParserTest {

    private val validJar =
        "# Netscape HTTP Cookie File\n" +
            "# A comment line\n" +
            "\n" +
            "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1781136000\tSID\tsample-sid-value\n" +
            ".youtube.com\tTRUE\t/\tTRUE\t0\tPREF\ttz=UTC\n" +
            ".google.com\tTRUE\t/\tTRUE\t1781136000\t__Secure-3PSID\tabcdef\n"

    @Test
    fun `valid jar is accepted with parsed attributes`() {
        val cookies = NetscapeJarParser.parse(validJar)
        assertEquals(3, cookies.size)

        val sid = cookies[0]
        assertEquals("SID", sid.name)
        assertEquals(".youtube.com", sid.domain)
        assertTrue(sid.httpOnly)
        assertTrue(sid.secure)
        assertEquals(1781136000L, sid.expirationDate)

        val pref = cookies[1]
        assertTrue(pref.session)
        assertEquals(null, pref.expirationDate)

        assertEquals(2, countYoutubeCookies(cookies))
    }

    @Test
    fun `round-trip through the serializer preserves data lines`() {
        val cookies = NetscapeJarParser.parse(validJar)
        val reserialized = serializeNetscapeCookies(cookies)
        val dataLines = { blob: String ->
            blob.lines().filter { it.isNotBlank() }
                .filter { !it.startsWith("#") || it.startsWith("#HttpOnly_") }
        }
        assertEquals(dataLines(validJar), dataLines(reserialized))
    }

    @Test
    fun `jar without youtube cookies is rejected with a value-free message`() {
        val googleOnly = ".google.com\tTRUE\t/\tTRUE\t0\tNID\txyz\n"
        val e = assertThrows(CookieParseException::class.java) {
            NetscapeJarParser.parse(googleOnly)
        }
        assertEquals("no youtube.com cookies present", e.message)
    }

    @Test
    fun `garbage is rejected with a structural message`() {
        val e = assertThrows(CookieParseException::class.java) {
            NetscapeJarParser.parse("this is not a cookie jar")
        }
        assertEquals("line 1: expected 7 tab-separated fields, got 1", e.message)
    }

    @Test
    fun `empty and comment-only blobs are rejected`() {
        val e = assertThrows(CookieParseException::class.java) {
            NetscapeJarParser.parse("# Netscape HTTP Cookie File\n\n")
        }
        assertEquals("no cookie entries found", e.message)
    }

    @Test
    fun `bad flags and bad expiry are rejected without echoing values`() {
        val badFlag = ".youtube.com\tYES\t/\tTRUE\t0\tSID\tsecret-value\n"
        val flagError = assertThrows(CookieParseException::class.java) {
            NetscapeJarParser.parse(badFlag)
        }
        assertEquals("line 1: include-subdomains flag must be TRUE/FALSE", flagError.message)
        assertTrue(!flagError.message!!.contains("secret-value"))

        val badExpiry = ".youtube.com\tTRUE\t/\tTRUE\tsoon\tSID\tsecret-value\n"
        val expiryError = assertThrows(CookieParseException::class.java) {
            NetscapeJarParser.parse(badExpiry)
        }
        assertEquals("line 1: expiry must be an integer timestamp", expiryError.message)
        assertTrue(!expiryError.message!!.contains("secret-value"))
    }

    @Test
    fun `host-only flag and CRLF input are handled`() {
        val jar = "music.youtube.com\tFALSE\t/\tFALSE\t0\twide\t1\r\n"
        val cookies = NetscapeJarParser.parse(jar)
        assertEquals(1, cookies.size)
        assertTrue(cookies[0].hostOnly)
        assertEquals("music.youtube.com\tFALSE\t/\tFALSE\t0\twide\t1", cookieToNetscapeLine(cookies[0]))
    }
}
