// Android-side bridge: query CookieManager for the YouTube/Google URLs and
// hand the header strings to the pure-Kotlin synthesizer.
package com.befeast.karaoke.submitter

import android.webkit.CookieManager
import com.befeast.karaoke.submitter.core.Cookie
import com.befeast.karaoke.submitter.core.CookieHeaderParser

object WebViewCookies {

    /**
     * Cookies currently held by the system WebView for the queried
     * YouTube/Google origins, with synthesized Netscape attributes. Empty when
     * WebView is unavailable or nothing is stored.
     */
    fun current(): List<Cookie> {
        val manager = try {
            CookieManager.getInstance()
        } catch (_: Throwable) {
            // No system WebView installed/enabled on this device.
            return emptyList()
        }
        val headers = CookieHeaderParser.QUERY_URLS.map { (url, domain) ->
            domain to manager.getCookie(url)
        }
        return CookieHeaderParser.synthesize(headers)
    }
}
