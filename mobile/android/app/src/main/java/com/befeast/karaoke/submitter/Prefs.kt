// Encrypted settings store: base URL, ktx_ access pass, and the optional
// pasted cookies.txt jar. Everything secret lives in EncryptedSharedPreferences
// (MasterKey AES256-GCM) and is NEVER logged or echoed into exceptions —
// mirror of the server's value-free discipline.
package com.befeast.karaoke.submitter

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

object Prefs {

    const val DEFAULT_BASE_URL = "https://karaoke.oklabs.uk"

    private const val FILE = "karaoke-submitter"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_TOKEN = "token"
    private const val KEY_PASTED_JAR = "pasted_jar"

    @Volatile
    private var cached: SharedPreferences? = null

    private fun prefs(context: Context): SharedPreferences {
        cached?.let { return it }
        synchronized(this) {
            cached?.let { return it }
            val appContext = context.applicationContext
            val masterKey = MasterKey.Builder(appContext)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            val created = EncryptedSharedPreferences.create(
                appContext,
                FILE,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
            cached = created
            return created
        }
    }

    fun baseUrl(context: Context): String =
        prefs(context).getString(KEY_BASE_URL, null)?.takeIf { it.isNotBlank() }
            ?: DEFAULT_BASE_URL

    fun token(context: Context): String =
        prefs(context).getString(KEY_TOKEN, null).orEmpty()

    fun setSettings(context: Context, baseUrl: String, token: String) {
        prefs(context).edit()
            .putString(KEY_BASE_URL, baseUrl.trim().trimEnd('/'))
            .putString(KEY_TOKEN, token.trim())
            .apply()
    }

    /** Normalized pasted cookies.txt jar, or null when none is stored. */
    fun pastedJar(context: Context): String? =
        prefs(context).getString(KEY_PASTED_JAR, null)?.takeIf { it.isNotBlank() }

    fun setPastedJar(context: Context, jar: String?) {
        prefs(context).edit().apply {
            if (jar.isNullOrBlank()) remove(KEY_PASTED_JAR) else putString(KEY_PASTED_JAR, jar)
        }.apply()
    }
}
