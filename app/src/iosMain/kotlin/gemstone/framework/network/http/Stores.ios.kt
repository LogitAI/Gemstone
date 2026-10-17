package gemstone.framework.network.http

import gemstone.framework.network.SecretStore
import gemstone.framework.network.SettingsStore
import gemstone.framework.network.settings.KeychainSecretStore
import gemstone.framework.network.settings.UserDefaultsStore


internal actual fun platformSettingsStore(): SettingsStore = UserDefaultsStore()


internal actual fun platformSecretStore(): SecretStore = KeychainSecretStore()
