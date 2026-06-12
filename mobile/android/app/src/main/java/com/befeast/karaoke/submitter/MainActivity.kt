// Main screen: URL field + Submit, share-target entry point (ACTION_SEND
// text/plain), YouTube sign-in status, manual cookies.txt paste fallback, and
// a last-5 jobs mini-feed.
package com.befeast.karaoke.submitter

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import com.befeast.karaoke.submitter.core.Cookie
import com.befeast.karaoke.submitter.core.CookieParseException
import com.befeast.karaoke.submitter.core.NetscapeJarParser
import com.befeast.karaoke.submitter.core.ShareText
import com.befeast.karaoke.submitter.core.buildJobBody
import com.befeast.karaoke.submitter.core.countYoutubeCookies
import com.befeast.karaoke.submitter.core.serializeNetscapeCookies

class MainActivity : Activity() {

    private lateinit var urlInput: EditText
    private lateinit var submitButton: Button
    private lateinit var receiptView: TextView
    private lateinit var statusView: TextView
    private lateinit var clearPasteButton: Button
    private lateinit var feedContainer: LinearLayout

    private var sharedTitle: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        urlInput = findViewById(R.id.url_input)
        submitButton = findViewById(R.id.submit_button)
        receiptView = findViewById(R.id.receipt_view)
        statusView = findViewById(R.id.signin_status)
        clearPasteButton = findViewById(R.id.clear_paste_button)
        feedContainer = findViewById(R.id.jobs_feed)

        submitButton.setOnClickListener { submit() }
        findViewById<Button>(R.id.sign_in_button).setOnClickListener {
            startActivity(Intent(this, SignInActivity::class.java))
        }
        findViewById<Button>(R.id.settings_button).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        findViewById<Button>(R.id.paste_button).setOnClickListener { showPasteDialog() }
        clearPasteButton.setOnClickListener {
            Prefs.setPastedJar(this, null)
            refreshCookieStatus()
            Toast.makeText(this, R.string.paste_cleared, Toast.LENGTH_SHORT).show()
        }

        handleShareIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShareIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        refreshCookieStatus()
        refreshFeed()
    }

    private fun handleShareIntent(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND || intent.type?.startsWith("text/") != true) {
            return
        }
        val text = intent.getStringExtra(Intent.EXTRA_TEXT)
        val url = ShareText.extractFirstUrl(text)
        if (url == null) {
            Toast.makeText(this, R.string.share_no_url, Toast.LENGTH_LONG).show()
            return
        }
        urlInput.setText(url)
        sharedTitle = intent.getStringExtra(Intent.EXTRA_SUBJECT)?.takeIf { it.isNotBlank() }
    }

    /** Pasted jar wins when present (explicit user action); else WebView cookies. */
    private fun cookiesForSubmit(): List<Cookie> {
        val pasted = Prefs.pastedJar(this)
        if (pasted != null) {
            try {
                return NetscapeJarParser.parse(pasted)
            } catch (_: CookieParseException) {
                // Stored jar no longer parses (should not happen — it was
                // validated at paste time); fall back to WebView cookies.
            }
        }
        return WebViewCookies.current()
    }

    private fun submit() {
        val url = ShareText.extractFirstUrl(urlInput.text?.toString())
        if (url == null) {
            receiptView.visibility = View.VISIBLE
            receiptView.text = getString(R.string.error_bad_url)
            return
        }
        val token = Prefs.token(this)
        if (token.isEmpty()) {
            receiptView.visibility = View.VISIBLE
            receiptView.text = getString(R.string.error_no_token)
            return
        }
        val body = buildJobBody(
            url = url,
            source = "android-app",
            title = sharedTitle,
            cookies = cookiesForSubmit(),
        )
        submitButton.isEnabled = false
        receiptView.visibility = View.VISIBLE
        receiptView.text = getString(R.string.submitting)
        val api = ApiClient(Prefs.baseUrl(this), token)
        Thread {
            val result = try {
                val receipt = api.submitJob(body)
                getString(
                    R.string.receipt_format,
                    receipt.id,
                    receipt.title ?: url,
                    receipt.status,
                )
            } catch (e: ApiException) {
                e.message
            }
            runOnUiThread {
                submitButton.isEnabled = true
                receiptView.text = result
                sharedTitle = null
                refreshFeed()
            }
        }.start()
    }

    private fun refreshCookieStatus() {
        val pasted = Prefs.pastedJar(this)
        if (pasted != null) {
            val count = try {
                countYoutubeCookies(NetscapeJarParser.parse(pasted))
            } catch (_: CookieParseException) {
                0
            }
            statusView.text = getString(R.string.status_pasted_jar, count)
            clearPasteButton.visibility = View.VISIBLE
            return
        }
        clearPasteButton.visibility = View.GONE
        val count = countYoutubeCookies(WebViewCookies.current())
        statusView.text = if (count > 0) {
            getString(R.string.status_signed_in, count)
        } else {
            getString(R.string.status_not_signed_in)
        }
    }

    private fun refreshFeed() {
        val token = Prefs.token(this)
        if (token.isEmpty()) {
            feedContainer.removeAllViews()
            addFeedLine(getString(R.string.feed_no_token))
            return
        }
        val api = ApiClient(Prefs.baseUrl(this), token)
        Thread {
            val rows = try {
                api.recentJobs(limit = 5)
            } catch (e: ApiException) {
                runOnUiThread {
                    feedContainer.removeAllViews()
                    addFeedLine(e.message ?: getString(R.string.feed_error))
                }
                return@Thread
            }
            runOnUiThread {
                feedContainer.removeAllViews()
                if (rows.isEmpty()) {
                    addFeedLine(getString(R.string.feed_empty))
                }
                for (row in rows) {
                    val view = TextView(this)
                    view.text = getString(
                        R.string.feed_row_format,
                        row.title ?: row.sourceUrl,
                        row.status,
                    )
                    view.setPadding(0, 12, 0, 12)
                    view.setOnClickListener {
                        try {
                            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(row.shareUrl)))
                        } catch (_: Exception) {
                            Toast.makeText(this, R.string.feed_open_failed, Toast.LENGTH_SHORT)
                                .show()
                        }
                    }
                    feedContainer.addView(view)
                }
            }
        }.start()
    }

    private fun addFeedLine(text: String) {
        val view = TextView(this)
        view.text = text
        view.setPadding(0, 12, 0, 12)
        feedContainer.addView(view)
    }

    private fun showPasteDialog() {
        val input = EditText(this)
        input.inputType =
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
        input.minLines = 6
        input.hint = getString(R.string.paste_hint)
        AlertDialog.Builder(this)
            .setTitle(R.string.paste_title)
            .setView(input)
            .setPositiveButton(R.string.paste_save) { _, _ ->
                val blob = input.text?.toString().orEmpty()
                try {
                    val cookies = NetscapeJarParser.parse(blob)
                    // Store the normalized serialization, not the raw paste.
                    Prefs.setPastedJar(this, serializeNetscapeCookies(cookies))
                    Toast.makeText(
                        this,
                        getString(R.string.paste_accepted, countYoutubeCookies(cookies)),
                        Toast.LENGTH_LONG,
                    ).show()
                } catch (e: CookieParseException) {
                    Toast.makeText(
                        this,
                        getString(R.string.paste_rejected, e.message),
                        Toast.LENGTH_LONG,
                    ).show()
                }
                refreshCookieStatus()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }
}
