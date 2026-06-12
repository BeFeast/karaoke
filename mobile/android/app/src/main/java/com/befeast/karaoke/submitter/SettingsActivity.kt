// Settings screen: base URL + ktx_ access pass, persisted in
// EncryptedSharedPreferences. The pass is rendered masked and never logged.
package com.befeast.karaoke.submitter

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast

class SettingsActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        val baseUrlInput = findViewById<EditText>(R.id.base_url_input)
        val tokenInput = findViewById<EditText>(R.id.token_input)
        val helpView = findViewById<TextView>(R.id.settings_help)

        baseUrlInput.setText(Prefs.baseUrl(this))
        tokenInput.setText(Prefs.token(this))
        helpView.text = getString(R.string.settings_help, Prefs.baseUrl(this))

        findViewById<Button>(R.id.save_button).setOnClickListener {
            val baseUrl = baseUrlInput.text?.toString()?.trim().orEmpty()
                .ifEmpty { Prefs.DEFAULT_BASE_URL }
            Prefs.setSettings(this, baseUrl, tokenInput.text?.toString().orEmpty())
            Toast.makeText(this, R.string.settings_saved, Toast.LENGTH_SHORT).show()
            finish()
        }
    }
}
