package gemstone.framework.network.settings

import com.sun.jna.Library
import com.sun.jna.Native
import com.sun.jna.Pointer
import com.sun.jna.platform.win32.Crypt32Util
import com.sun.jna.ptr.IntByReference
import com.sun.jna.ptr.PointerByReference
import gemstone.framework.network.MemorySecretStore
import gemstone.framework.network.SecretStore
import gemstone.framework.network.ServerSettings.Companion.KEY_ACCOUNT
import gemstone.framework.network.ServerSettings.Companion.KEY_SERVICE
import gemstone.framework.network.SettingsStore
import java.io.File
import java.util.Base64
import java.util.prefs.Preferences


/** The server address in `java.util.prefs`, under the user's node for Gemstone. */
class PreferencesStore(private val node: Preferences = Preferences.userRoot().node(NODE)) : SettingsStore {
    override fun get(key: String): String? = node.get(key, null)

    override fun set(key: String, value: String?) {
        if (value == null) node.remove(key) else node.put(key, value)
        node.flush()
    }

    companion object {
        const val NODE = "io/github/thisisthepy/gemstone"
    }
}


/**
 * The desktop's secure storage for the API key (SPEC S2.7): the login Keychain on macOS, DPAPI on
 * Windows, the Secret Service on Linux. When none can be used, the key is kept for this run only.
 */
fun desktopSecretStore(preferences: SettingsStore): SecretStore {
    val os = System.getProperty("os.name").orEmpty().lowercase()
    return try {
        when {
            "mac" in os -> MacKeychainStore()
            "windows" in os -> WindowsDpapiStore(preferences)
            else -> SecretToolStore.ifAvailable() ?: MemorySecretStore()
        }
    } catch (e: Throwable) {  // the native library cannot be loaded
        println("WARNING: No secure storage for the API key, keeping it for this run only: ${e.message}")
        MemorySecretStore()
    }
}


/** The login Keychain, through Security.framework (a generic password). */
class MacKeychainStore : SecretStore {
    @Suppress("FunctionName")
    private interface Security : Library {
        fun SecKeychainFindGenericPassword(
            keychainOrArray: Pointer?, serviceNameLength: Int, serviceName: ByteArray,
            accountNameLength: Int, accountName: ByteArray,
            passwordLength: IntByReference?, passwordData: PointerByReference?, itemRef: PointerByReference?
        ): Int

        fun SecKeychainAddGenericPassword(
            keychain: Pointer?, serviceNameLength: Int, serviceName: ByteArray,
            accountNameLength: Int, accountName: ByteArray,
            passwordLength: Int, passwordData: ByteArray, itemRef: Pointer?
        ): Int

        fun SecKeychainItemModifyAttributesAndData(itemRef: Pointer, attrList: Pointer?, length: Int, data: ByteArray): Int
        fun SecKeychainItemDelete(itemRef: Pointer): Int
        fun SecKeychainItemFreeContent(attrList: Pointer?, data: Pointer?): Int
    }

    @Suppress("FunctionName")
    private interface CoreFoundation : Library {
        fun CFRelease(cf: Pointer)
    }

    private val security = Native.load("Security", Security::class.java)
    private val coreFoundation = Native.load("CoreFoundation", CoreFoundation::class.java)
    private val service = KEY_SERVICE.encodeToByteArray()
    private val account = KEY_ACCOUNT.encodeToByteArray()

    override val persistent: Boolean = true

    override fun get(): String? {
        val length = IntByReference()
        val data = PointerByReference()
        val status = security.SecKeychainFindGenericPassword(
            null, service.size, service, account.size, account, length, data, null
        )
        if (status != 0) return null  // errSecItemNotFound (-25300) or no access
        return try {
            data.value.getByteArray(0, length.value).decodeToString()
        } finally {
            security.SecKeychainItemFreeContent(null, data.value)
        }
    }

    override fun set(value: String?) {
        val item = PointerByReference()
        val found = security.SecKeychainFindGenericPassword(
            null, service.size, service, account.size, account, null, null, item
        ) == 0
        try {
            val status = when {
                value == null -> if (found) security.SecKeychainItemDelete(item.value) else 0
                found -> value.encodeToByteArray().let {
                    security.SecKeychainItemModifyAttributesAndData(item.value, null, it.size, it)
                }
                else -> value.encodeToByteArray().let {
                    security.SecKeychainAddGenericPassword(null, service.size, service, account.size, account, it.size, it, null)
                }
            }
            check(status == 0) { "Keychain error $status" }
        } finally {
            if (found) coreFoundation.CFRelease(item.value)
        }
    }
}


/** DPAPI for the current Windows user; only the ciphertext is written to the preferences. */
class WindowsDpapiStore(private val preferences: SettingsStore) : SecretStore {
    override val persistent: Boolean = true

    override fun get(): String? = preferences.get(PREFERENCE)?.let {
        runCatching { Crypt32Util.cryptUnprotectData(Base64.getDecoder().decode(it)).decodeToString() }.getOrNull()
    }

    override fun set(value: String?) {
        preferences.set(PREFERENCE, value?.let {
            Base64.getEncoder().encodeToString(Crypt32Util.cryptProtectData(it.encodeToByteArray()))
        })
    }

    companion object {
        const val PREFERENCE = "api_key_dpapi"
    }
}


/** The Secret Service (libsecret) through `secret-tool`; the key goes over stdin, never the command line. */
class SecretToolStore private constructor(private val tool: String) : SecretStore {
    private val attributes = arrayOf("service", KEY_SERVICE, "account", KEY_ACCOUNT)

    override val persistent: Boolean = true

    override fun get(): String? {
        val process = ProcessBuilder(tool, "lookup", *attributes).redirectError(ProcessBuilder.Redirect.DISCARD).start()
        val output = process.inputStream.readBytes().decodeToString()
        return if (process.waitFor() == 0) output.trimEnd('\n').ifEmpty { null } else null
    }

    override fun set(value: String?) {
        val process = if (value == null) {
            ProcessBuilder(tool, "clear", *attributes).redirectError(ProcessBuilder.Redirect.DISCARD).start()
        } else {
            ProcessBuilder(tool, "store", "--label=Gemstone API key", *attributes)
                .redirectError(ProcessBuilder.Redirect.DISCARD).start()
                .also { p -> p.outputStream.use { it.write(value.encodeToByteArray()) } }
        }
        // `clear` fails when there is nothing to clear, which is not an error
        check(process.waitFor() == 0 || value == null) { "secret-tool store failed" }
    }

    companion object {
        fun ifAvailable(): SecretToolStore? = System.getenv("PATH").orEmpty().split(File.pathSeparator)
            .map { File(it, "secret-tool") }
            .firstOrNull { it.canExecute() }
            ?.let { SecretToolStore(it.path) }
    }
}
