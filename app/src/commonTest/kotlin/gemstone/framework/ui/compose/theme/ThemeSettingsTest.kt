package gemstone.framework.ui.compose.theme

import gemstone.framework.network.MemoryStore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue


class ThemeSettingsTest {
    private val store = MemoryStore()

    @Test
    fun withNothingSavedTheSystemIsFollowed() {
        val settings = ThemeSettings(store).also { it.load() }
        assertEquals(ThemeMode.System, settings.mode)
    }

    @Test
    fun aSavedModeComesBackAfterARestart() {
        ThemeSettings(store).also { it.load() }.save(ThemeMode.Dark)
        assertEquals(ThemeMode.Dark, ThemeSettings(store).also { it.load() }.mode)
    }

    @Test
    fun theSavedChoiceAppliesAtOnce() {
        val settings = ThemeSettings(store).also { it.load() }
        settings.save(ThemeMode.Light)
        assertEquals(ThemeMode.Light, settings.mode)
    }

    @Test
    fun anUnknownStoredValueFallsBackToTheSystem() {
        store.set(ThemeSettings.KEY, "Orange")
        assertEquals(ThemeMode.System, ThemeSettings(store).also { it.load() }.mode)
    }

    @Test
    fun theSystemModeFollowsTheSystemAndTheOthersDoNot() {
        assertTrue(ThemeSettings.isDark(ThemeMode.System, systemDark = true))
        assertFalse(ThemeSettings.isDark(ThemeMode.System, systemDark = false))
        assertTrue(ThemeSettings.isDark(ThemeMode.Dark, systemDark = false))
        assertFalse(ThemeSettings.isDark(ThemeMode.Light, systemDark = true))
    }
}
