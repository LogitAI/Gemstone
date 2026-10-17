package gemstone.framework.network.http

import gemstone.framework.network.SecretStore
import gemstone.framework.network.SettingsStore
import gemstone.framework.network.settings.PreferencesStore
import gemstone.framework.network.settings.desktopSecretStore


private val preferences by lazy { PreferencesStore() }


internal actual fun platformSettingsStore(): SettingsStore = preferences


internal actual fun platformSecretStore(): SecretStore = desktopSecretStore(preferences)
