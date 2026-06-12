package com.befeast.karaoke.submitter.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** ACTION_SEND share-text URL extraction. */
class ShareTextTest {

    @Test
    fun `bare url is returned as-is`() {
        assertEquals(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ShareText.extractFirstUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        )
    }

    @Test
    fun `youtube app share format - title then url`() {
        val shared = "Watch \"Never Gonna Give You Up\" on YouTube\nhttps://youtu.be/dQw4w9WgXcQ?si=AbC"
        assertEquals("https://youtu.be/dQw4w9WgXcQ?si=AbC", ShareText.extractFirstUrl(shared))
    }

    @Test
    fun `youtu_be short links work`() {
        assertEquals("https://youtu.be/abc123", ShareText.extractFirstUrl("https://youtu.be/abc123"))
    }

    @Test
    fun `first url token wins when several are present`() {
        assertEquals(
            "http://example.com/a",
            ShareText.extractFirstUrl("see http://example.com/a and https://example.com/b"),
        )
    }

    @Test
    fun `non-url text yields null`() {
        assertNull(ShareText.extractFirstUrl("just some words"))
        assertNull(ShareText.extractFirstUrl(""))
        assertNull(ShareText.extractFirstUrl(null))
    }

    @Test
    fun `scheme without host yields null`() {
        assertNull(ShareText.extractFirstUrl("https://"))
        assertNull(ShareText.extractFirstUrl("http:// then nothing"))
    }

    @Test
    fun `non-http schemes are ignored`() {
        assertNull(ShareText.extractFirstUrl("ftp://example.com/file"))
        assertEquals(
            "https://example.com/x",
            ShareText.extractFirstUrl("ftp://example.com/file https://example.com/x"),
        )
    }

    @Test
    fun `any yt-dlp-supported host is allowed`() {
        assertEquals(
            "https://vimeo.com/12345",
            ShareText.extractFirstUrl("Look https://vimeo.com/12345"),
        )
    }
}
