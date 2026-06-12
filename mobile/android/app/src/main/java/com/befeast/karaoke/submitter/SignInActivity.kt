// WebView "Sign in to YouTube" screen. Google blocks stock-WebView sign-in
// ("This browser or app may not be secure" / disallowed_useragent), so two
// standard mitigations are applied: the user-agent drops the `; wv` token (and
// the `Version/4.0` WebView marker), and the X-Requested-With app-package
// header is suppressed via androidx.webkit where supported — both are WebView
// fingerprints Google checks. Cookie-session login (not OAuth) works with
// these in current practice; if Google still hard-blocks, the manual
// cookies.txt paste on the main screen is the fallback.
package com.befeast.karaoke.submitter

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import androidx.webkit.WebSettingsCompat

class SignInActivity : Activity() {

    private lateinit var webView: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_sign_in)

        webView = findViewById(R.id.web_view)
        findViewById<Button>(R.id.done_button).setOnClickListener {
            CookieManager.getInstance().flush()
            finish()
        }

        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.userAgentString = chromeLikeUserAgent()
        // WebViewFeature.REQUESTED_WITH_HEADER_ALLOW_LIST is @RestrictTo in
        // androidx.webkit, so the documented UnsupportedOperationException IS
        // the feature-support check here.
        try {
            WebSettingsCompat.setRequestedWithHeaderOriginAllowList(settings, emptySet())
        } catch (_: UnsupportedOperationException) {
            // WebView too old for the allow-list control — the UA override
            // above still applies.
        }

        val cookieManager = CookieManager.getInstance()
        cookieManager.setAcceptCookie(true)
        cookieManager.setAcceptThirdPartyCookies(webView, true)

        webView.webViewClient = WebViewClient()
        webView.loadUrl(SIGN_IN_URL)
    }

    /**
     * The device's real WebView UA with the WebView fingerprints removed:
     * drop the `; wv` token and the `Version/4.0 ` marker so the string reads
     * as the device's current mobile Chrome.
     */
    private fun chromeLikeUserAgent(): String =
        WebSettings.getDefaultUserAgent(this)
            .replace("; wv", "")
            .replace(Regex("Version/\\d+\\.\\d+ "), "")

    override fun onDestroy() {
        CookieManager.getInstance().flush()
        super.onDestroy()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }

    companion object {
        private const val SIGN_IN_URL =
            "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/"
    }
}
