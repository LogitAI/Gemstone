package gemstone.framework.ui.compose.theme

import androidx.compose.ui.graphics.luminance
import gemstone.framework.network.MemoryStore
import gemstone.framework.ui.compose.theme.AppColorSet.Companion.ColorSet
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue


class ThemeSettingsTest {
    private val store = MemoryStore()

    @Test
    fun withNothingSavedTheSystemAndBlackAreUsed() {
        val settings = ThemeSettings(store).also { it.load() }
        assertEquals(ThemeMode.System, settings.mode)
        assertEquals(ColorSet.Black, settings.colorSet)
    }

    @Test
    fun aSavedModeAndColorSetComeBackAfterARestart() {
        ThemeSettings(store).also { it.load() }.save(ThemeMode.Dark, ColorSet.Blue)
        val reloaded = ThemeSettings(store).also { it.load() }
        assertEquals(ThemeMode.Dark, reloaded.mode)
        assertEquals(ColorSet.Blue, reloaded.colorSet)
    }

    @Test
    fun theSavedChoiceAppliesAtOnce() {
        val settings = ThemeSettings(store).also { it.load() }
        settings.save(ThemeMode.Light, ColorSet.Orange)
        assertEquals(ThemeMode.Light, settings.mode)
        assertEquals(ColorSet.Orange, settings.colorSet)
        settings.saveColorSet(ColorSet.Blue)
        assertEquals(ColorSet.Blue, settings.colorSet)
        assertEquals(ColorSet.Blue, ThemeSettings(store).also { it.load() }.colorSet)
    }

    @Test
    fun anUnknownStoredValueFallsBackToTheDefaults() {
        store.set(ThemeSettings.KEY, "Orange")
        store.set(ThemeSettings.COLOR_SET_KEY, "Purple")
        val settings = ThemeSettings(store).also { it.load() }
        assertEquals(ThemeMode.System, settings.mode)
        assertEquals(ColorSet.Black, settings.colorSet)
    }

    @Test
    fun theSystemModeFollowsTheSystemAndTheOthersDoNot() {
        assertTrue(ThemeSettings.isDark(ThemeMode.System, systemDark = true))
        assertFalse(ThemeSettings.isDark(ThemeMode.System, systemDark = false))
        assertTrue(ThemeSettings.isDark(ThemeMode.Dark, systemDark = false))
        assertFalse(ThemeSettings.isDark(ThemeMode.Light, systemDark = true))
    }

    @Test
    fun everyColorSetHasDistinctLightAndDarkPalettes() {
        val palettes = mutableSetOf<AppColorSet>()
        for (set in ColorSet.entries) {
            val light = AppColorSet.paletteFor(set, isLight = true)
            val dark = AppColorSet.paletteFor(set, isLight = false)
            assertNotEquals(light, dark)
            assertTrue(light.materialColorScheme.background.luminance() > 0.7f, "$set light background should be bright")
            assertTrue(dark.materialColorScheme.background.luminance() < 0.2f, "$set dark background should be dark")
            assertTrue(palettes.add(light), "$set light palette must be distinct")
            assertTrue(palettes.add(dark), "$set dark palette must be distinct")
        }
    }
}
