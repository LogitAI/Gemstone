package gemstone.framework.storage

import gemstone.framework.network.SettingsStore
import gemstone.framework.network.settings.UserDefaultsStore


actual fun platformPreferences(): SettingsStore = UserDefaultsStore()
