package gemstone.framework.ui.compose.theme

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import gemstone.framework.network.SettingsStore
import gemstone.framework.storage.platformPreferences


/** Light, dark, or following the system (SPEC S2.7). */
enum class ThemeMode { System, Light, Dark }


/** The theme mode, kept in the platform's preferences. */
class ThemeSettings(private val store: SettingsStore) {
    // State, so the theme recomposes when it changes
    var mode: ThemeMode by mutableStateOf(ThemeMode.System)
        private set

    fun load() {
        // An unknown value (a palette that no longer exists, a newer app's mode) means the system
        mode = ThemeMode.entries.firstOrNull { it.name == store.get(KEY) } ?: ThemeMode.System
    }

    fun save(mode: ThemeMode) {
        store.set(KEY, mode.name)
        this.mode = mode
    }

    companion object {
        const val KEY = "theme_mode"

        /** Whether the dark palette applies. */
        fun isDark(mode: ThemeMode, systemDark: Boolean): Boolean = when (mode) {
            ThemeMode.System -> systemDark
            ThemeMode.Light -> false
            ThemeMode.Dark -> true
        }
    }
}


/** The theme mode in effect, loaded on first use. */
val themeSettings: ThemeSettings by lazy { ThemeSettings(platformPreferences()).also { it.load() } }
