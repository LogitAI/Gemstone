package gemstone.framework.storage

import gemstone.framework.network.SettingsStore
import gemstone.framework.network.settings.AndroidSettings
import gemstone.framework.network.settings.SharedPreferencesStore


actual fun platformPreferences(): SettingsStore = SharedPreferencesStore(AndroidSettings.context)
