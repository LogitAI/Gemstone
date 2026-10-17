package gemstone.framework.storage

import gemstone.framework.network.SettingsStore
import gemstone.framework.network.settings.PreferencesStore


actual fun platformPreferences(): SettingsStore = PreferencesStore()
