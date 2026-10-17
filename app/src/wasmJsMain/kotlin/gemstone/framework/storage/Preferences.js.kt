package gemstone.framework.storage

import gemstone.framework.network.SettingsStore
import kotlinx.browser.localStorage


/** The browser's `localStorage` for the server's origin, keys prefixed `gemstone.`. */
class LocalStorageSettingsStore : SettingsStore {
    override fun get(key: String): String? = localStorage.getItem("gemstone.$key")

    override fun set(key: String, value: String?) {
        if (value == null) localStorage.removeItem("gemstone.$key") else localStorage.setItem("gemstone.$key", value)
    }
}


actual fun platformPreferences(): SettingsStore = LocalStorageSettingsStore()
