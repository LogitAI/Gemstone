package gemstone.framework.network.settings

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import gemstone.framework.network.SecretStore
import gemstone.framework.network.SettingsStore
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec


/** The application context, set by `MainActivity` before the first composition. */
object AndroidSettings {
    lateinit var context: Context
        private set

    fun init(context: Context) {
        this.context = context.applicationContext
    }
}


/** The server address in the app's `SharedPreferences`. */
class SharedPreferencesStore(context: Context) : SettingsStore {
    private val preferences = context.getSharedPreferences("gemstone_settings", Context.MODE_PRIVATE)

    override fun get(key: String): String? = preferences.getString(key, null)

    override fun set(key: String, value: String?) {
        preferences.edit().apply { if (value == null) remove(key) else putString(key, value) }.apply()
    }
}


/**
 * The API key encrypted with AES-GCM under a non-exportable Android Keystore key (SPEC S2.7). Only
 * the ciphertext and its IV are written to the app's preferences.
 */
class KeystoreSecretStore(context: Context) : SecretStore {
    private val preferences = context.getSharedPreferences("gemstone_secrets", Context.MODE_PRIVATE)

    override val persistent: Boolean = true

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keyStore.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build()
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE).apply { init(spec) }.generateKey()
    }

    override fun get(): String? {
        val ciphertext = preferences.getString(CIPHERTEXT, null) ?: return null
        val iv = preferences.getString(IV, null) ?: return null
        return runCatching {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)))
            cipher.doFinal(Base64.decode(ciphertext, Base64.NO_WRAP)).decodeToString()
        }.getOrNull()  // the Keystore key is gone (app data restored elsewhere): no key
    }

    override fun set(value: String?) {
        if (value == null) {
            preferences.edit().remove(CIPHERTEXT).remove(IV).apply()
            return
        }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val ciphertext = cipher.doFinal(value.encodeToByteArray())
        preferences.edit()
            .putString(CIPHERTEXT, Base64.encodeToString(ciphertext, Base64.NO_WRAP))
            .putString(IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .apply()
    }

    companion object {
        private const val KEYSTORE = "AndroidKeyStore"
        private const val ALIAS = "gemstone_api_key"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val CIPHERTEXT = "api_key"
        private const val IV = "api_key_iv"
    }
}
