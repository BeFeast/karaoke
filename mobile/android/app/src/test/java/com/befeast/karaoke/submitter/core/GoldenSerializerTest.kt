package com.befeast.karaoke.submitter.core

import org.junit.Assert.assertArrayEquals
import org.junit.Test

/**
 * Golden test: serializer output must be byte-for-byte equal to the committed
 * fixture (issue #169). Lines 3–6 of the fixture are byte-identical to what
 * extension/chrome/cookies.js produces for the same input — expiry capped on
 * line 4, leading dot forced on line 6, tab stripped from the value.
 */
class GoldenSerializerTest {

    private val goldenInput = listOf(
        Cookie(
            name = "SID", value = "sample-sid-value", domain = ".youtube.com",
            path = "/", secure = true, httpOnly = true, hostOnly = false,
            session = false, expirationDate = 1781136000L,
        ),
        Cookie(
            name = "PREF", value = "tz=UTC", domain = ".youtube.com",
            path = "/", secure = true, httpOnly = false, hostOnly = false,
            session = false, expirationDate = 9999999999L,
        ),
        Cookie(
            name = "wide", value = "1", domain = "music.youtube.com",
            path = "/", secure = false, httpOnly = false, hostOnly = true,
            session = true, expirationDate = null,
        ),
        Cookie(
            name = "__Secure-3PSID", value = "abc\tdef", domain = "google.com",
            path = "/", secure = true, httpOnly = true, hostOnly = false,
            session = false, expirationDate = 1781136000L,
        ),
    )

    @Test
    fun `serializer output is byte-equal to the committed golden fixture`() {
        val expected = javaClass.getResourceAsStream("/golden-cookies.txt")!!
            .use { it.readBytes() }
        val actual = serializeNetscapeCookies(goldenInput).toByteArray(Charsets.UTF_8)
        assertArrayEquals(expected, actual)
    }
}
