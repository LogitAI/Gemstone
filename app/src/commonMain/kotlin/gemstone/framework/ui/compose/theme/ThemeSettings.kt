package gemstone.framework.ui.compose.theme

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import gemstone.framework.network.SettingsStore
import gemstone.framework.storage.platformPreferences
import gemstone.framework.ui.compose.theme.AppColorSet.Companion.ColorSet


/** Light, dark, or following the system (SPEC S2.7). */
enum class ThemeMode { System, Light, Dark }


/** The theme mode and color set, kept in the platform's preferences (SPEC S2.7). */
class ThemeSettings(private val store: SettingsStore) {
    // State, so the theme recomposes when it changes
    var mode: ThemeMode by mutableStateOf(ThemeMode.System)
        private set
    var colorSet: ColorSet by mutableStateOf(ColorSet.Black)
        private set

    fun load() {
        // An unknown value (a palette that no longer exists, a newer app's mode) falls back to the default
        mode = ThemeMode.entries.firstOrNull { it.name == store.get(KEY) } ?: ThemeMode.System
        colorSet = ColorSet.entries.firstOrNull { it.name == store.get(COLOR_SET_KEY) } ?: ColorSet.Black
    }

    fun save(mode: ThemeMode, colorSet: ColorSet = this.colorSet) {
        store.set(KEY, mode.name)
        store.set(COLOR_SET_KEY, colorSet.name)
        this.mode = mode
        this.colorSet = colorSet
    }

    fun saveColorSet(colorSet: ColorSet) {
        store.set(COLOR_SET_KEY, colorSet.name)
        this.colorSet = colorSet
    }

    companion object {
        const val KEY = "theme_mode"
        const val COLOR_SET_KEY = "theme_color_set"

        /** Whether the dark palette applies. */
        fun isDark(mode: ThemeMode, systemDark: Boolean): Boolean = when (mode) {
            ThemeMode.System -> systemDark
            ThemeMode.Light -> false
            ThemeMode.Dark -> true
        }
    }
}


/** The theme settings in effect, loaded on first use. */
val themeSettings: ThemeSettings by lazy { ThemeSettings(platformPreferences()).also { it.load() } }
