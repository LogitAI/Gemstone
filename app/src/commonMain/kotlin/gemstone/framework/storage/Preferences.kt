package gemstone.framework.storage

import gemstone.framework.network.SettingsStore


/** The platform's preferences for values that are not secret (SPEC S2.7). */
expect fun platformPreferences(): SettingsStore
