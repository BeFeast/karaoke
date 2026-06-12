package com.befeast.karaoke.submitter.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Port of extension/chrome/cookies.test.js buildJobBody cases. */
class JobBodyTest {

    private val yt = Cookie(name = "SID", value = "abc", domain = ".youtube.com", secure = true)
    private val goog = Cookie(name = "SAPISID", value = "xyz", domain = ".google.com", secure = true)

    @Test
    fun `always includes the url`() {
        assertEquals("https://youtu.be/abc", buildJobBody("https://youtu.be/abc")["url"])
    }

    @Test
    fun `includes source and title only when provided`() {
        val withMeta = buildJobBody(
            "https://youtu.be/abc",
            source = "android-app",
            title = "Rick Astley",
        )
        assertEquals("android-app", withMeta["source"])
        assertEquals("Rick Astley", withMeta["title"])

        val bare = buildJobBody("https://youtu.be/abc")
        assertFalse(bare.containsKey("source"))
        assertFalse(bare.containsKey("title"))
    }

    @Test
    fun `attaches youtube_cookies when youtube cookies are present`() {
        val body = buildJobBody("https://youtu.be/abc", cookies = listOf(yt, goog))
        val blob = body["youtube_cookies"]!!
        assertTrue(blob.startsWith("# Netscape HTTP Cookie File\n"))
        // The attached blob is exactly the serialized merged jar (yt + google).
        assertEquals(serializeNetscapeCookies(listOf(yt, goog)), blob)
        assertTrue(blob.contains("\tSID\t"))
        assertTrue(blob.contains("\tSAPISID\t"))
    }

    @Test
    fun `omits youtube_cookies when there are no cookies at all`() {
        assertFalse(buildJobBody("https://youtu.be/abc").containsKey("youtube_cookies"))
        assertEquals(listOf("url"), buildJobBody("https://youtu.be/abc").keys.toList())
    }

    @Test
    fun `omits youtube_cookies when only non-youtube cookies are present`() {
        val body = buildJobBody("https://youtu.be/abc", cookies = listOf(goog))
        assertFalse(body.containsKey("youtube_cookies"))
    }
}
