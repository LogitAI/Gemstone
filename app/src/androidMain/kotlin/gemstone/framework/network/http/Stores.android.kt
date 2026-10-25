package gemstone.framework.network.http

import gemstone.framework.network.SecretStore
import gemstone.framework.network.SettingsStore
import gemstone.framework.network.settings.AndroidSettings
import gemstone.framework.network.settings.KeystoreSecretStore
import gemstone.framework.network.settings.SharedPreferencesStore


internal actual fun platformSettingsStore(): SettingsStore = SharedPreferencesStore(AndroidSettings.context)


internal actual fun platformSecretStore(): SecretStore = KeystoreSecretStore(AndroidSettings.context)
