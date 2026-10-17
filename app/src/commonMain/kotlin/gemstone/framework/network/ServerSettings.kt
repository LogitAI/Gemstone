package gemstone.framework.network


/** Plain preferences, for values that are not secret (the server address). */
interface SettingsStore {
    fun get(key: String): String?
    fun set(key: String, value: String?)
}


/** The platform's secure storage for the API key (SPEC S2.7): the key is never kept in plain text. */
interface SecretStore {
    /** False when the key is kept for this run only (desktop Linux without `secret-tool`). */
    val persistent: Boolean
    fun get(): String?
    fun set(value: String?)
}


/** Values kept for this run only: the web, and a desktop without a Secret Service. */
class MemorySettingsStore : SettingsStore {
    private val values = mutableMapOf<String, String>()
    override fun get(key: String): String? = values[key]
    override fun set(key: String, value: String?) {
        if (value == null) values.remove(key) else values[key] = value
    }
}


/** A key kept in memory for this run only, never written anywhere. */
class MemorySecretStore : SecretStore {
    private var value: String? = null
    override val persistent: Boolean = false
    override fun get(): String? = value
    override fun set(value: String?) {
        this.value = value
    }
}


/** What the app was started with (environment, system property, command line); null when not set. */
data class LaunchSettings(val address: ServerAddress? = null, val apiKey: String? = null)


/** The server address and API key in effect (SPEC S2.7). */
class ServerSettings(
    private val launch: LaunchSettings,
    private val store: SettingsStore,
    private val secrets: SecretStore,
    /** False on the web, where the address is the page's origin and no key is sent (S2.2). */
    val editable: Boolean = true
) {
    private var savedAddressText: String? = store.get(ADDRESS_KEY)
    private var savedKey: String? = runCatching { secrets.get() }.getOrNull()  // an unreadable store means no key

    /** True when the address cannot be changed here: set at launch, or the page's origin on the web. */
    val addressSetAtLaunch: Boolean get() = launch.address != null || !editable
    val keySetAtLaunch: Boolean get() = launch.apiKey != null
    /** False when a saved key lasts for this run only. */
    val keyPersistent: Boolean get() = secrets.persistent

    val address: ServerAddress
        get() = launch.address
            ?: savedAddressText?.let { runCatching { ServerAddress.parse(it) }.getOrNull() }
            ?: ServerAddress.resolve(null, null)

    val apiKey: String? get() = launch.apiKey ?: savedKey.takeIf { editable }

    /** The address as the dialog shows it: the launch value, else the saved text (empty: the default). */
    val addressText: String get() = launch.address?.toString() ?: savedAddressText.orEmpty()

    /**
     * Validates and saves; returns the error to show, or null when saved. A field set at launch (or
     * any field on the web) is left alone. An empty address means the default, an empty key no key.
     */
    fun save(addressText: String, apiKey: String): String? {
        val text = addressText.trim().ifEmpty { null }
        if (text != null && !addressSetAtLaunch) {
            try {
                ServerAddress.parse(text)
            } catch (e: IllegalArgumentException) {
                return e.message ?: "Invalid server address: $text"
            }
        }
        // The key first: when the secure storage fails, nothing is saved
        if (editable && !keySetAtLaunch) {
            val key = apiKey.trim().ifEmpty { null }
            try {
                secrets.set(key)
            } catch (e: Exception) {
                return "Could not store the API key: ${e.message}"
            }
            savedKey = key
        }
        if (!addressSetAtLaunch) {
            store.set(ADDRESS_KEY, text)
            savedAddressText = text
        }
        return null
    }

    companion object {
        const val ADDRESS_KEY = "server_address"
        /** Where the platform's secure storage keeps the key (Keychain service and account). */
        const val KEY_SERVICE = "io.github.thisisthepy.gemstone"
        const val KEY_ACCOUNT = "api-key"
    }
}
