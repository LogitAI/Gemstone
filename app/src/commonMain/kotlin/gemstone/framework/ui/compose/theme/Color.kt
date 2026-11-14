package gemstone.framework.ui.compose.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color


abstract class AppColorSet {
    abstract val materialColorScheme: ColorScheme

    val appBackgroundColorStart: Color
        get() = materialColorScheme.background
    abstract val appBackgroundColorEnd: Color

    companion object {
        enum class ColorSet {
            Black, Blue, Orange
        }

        val currentColorSet: ColorSet
            @Composable get() = themeSettings.colorSet

        val isLight  // the mode chosen in the settings (SPEC S2.7)
            @Composable get() = !ThemeSettings.isDark(themeSettings.mode, isSystemInDarkTheme())

        val currentColorScheme
            @Composable get() = MaterialTheme.colorScheme

        fun paletteFor(colorSet: ColorSet, isLight: Boolean): AppColorSet = when (colorSet) {
            ColorSet.Black -> if (isLight) BlackLightColorSet else BlackDarkColorSet
            ColorSet.Blue -> if (isLight) BlueLightColorSet else BlueDarkColorSet
            ColorSet.Orange -> if (isLight) OrangeLightColorSet else OrangeDarkColorSet
        }

        val currentAppColorSet: AppColorSet
            @Composable get() = paletteFor(currentColorSet, isLight)
    }
}


val appColorSet
    @Composable get() = AppColorSet.currentAppColorSet


object BlackLightColorSet : AppColorSet() {
    override val materialColorScheme = lightColorScheme(
        primary = Color(0xFF262626),
        onPrimary = Color(0xFFBEBEBE),
        secondary = Color.White,
        onSecondary = Color(0xFF414244),
        tertiary = Color(0xFFD6D6D6),
        onTertiary = Color(0xFF414244),
        background = Color(0xFFF2F2F2),
        onBackground = Color(0xFF262626),
        surface = Color(0xFFF7F7F7).copy(alpha = 0.9f),
        onSurface = Color(0xFF414244)
    )
    override val appBackgroundColorEnd: Color = Color(0xFFEFEFEF)
}


object BlackDarkColorSet : AppColorSet() {
    override val materialColorScheme = darkColorScheme(
        primary = Color(0xFFEAEAEA),
        onPrimary = Color(0xFF1F1F23),
        secondary = Color(0xFF26272C),
        onSecondary = Color(0xFFE2E2E6),
        tertiary = Color(0xFF35373E),
        onTertiary = Color(0xFFD4D5DA),
        background = Color(0xFF17181C),
        onBackground = Color(0xFFEAEAEA),
        surface = Color(0xE6222328),
        onSurface = Color(0xFFE2E2E6)
    )
    override val appBackgroundColorEnd: Color = Color(0xFF121316)
}


object BlueLightColorSet : AppColorSet() {
    override val materialColorScheme = lightColorScheme(
        primary = Color(0xFF1F4E8C),
        onPrimary = Color(0xFFDCE8F8),
        secondary = Color(0xFFFFFFFF),
        onSecondary = Color(0xFF2C3E55),
        tertiary = Color(0xFFD0DFEF),
        onTertiary = Color(0xFF2C3E55),
        background = Color(0xFFEEF3FA),
        onBackground = Color(0xFF1A2B42),
        surface = Color(0xFFF5F8FD).copy(alpha = 0.9f),
        onSurface = Color(0xFF2C3E55)
    )
    override val appBackgroundColorEnd: Color = Color(0xFFE4ECF7)
}


object BlueDarkColorSet : AppColorSet() {
    override val materialColorScheme = darkColorScheme(
        primary = Color(0xFF8AB4F8),
        onPrimary = Color(0xFF13233A),
        secondary = Color(0xFF1E293B),
        onSecondary = Color(0xFFE2E8F0),
        tertiary = Color(0xFF2D3D54),
        onTertiary = Color(0xFFCBD5E1),
        background = Color(0xFF111827),
        onBackground = Color(0xFFE5EDF8),
        surface = Color(0xE61B2536),
        onSurface = Color(0xFFE2E8F0)
    )
    override val appBackgroundColorEnd: Color = Color(0xFF0D1320)
}


object OrangeLightColorSet : AppColorSet() {
    override val materialColorScheme = lightColorScheme(
        primary = Color(0xFF9A3E12),
        onPrimary = Color(0xFFFCE7DC),
        secondary = Color(0xFFFFFFFF),
        onSecondary = Color(0xFF4A3429),
        tertiary = Color(0xFFEFD9CC),
        onTertiary = Color(0xFF4A3429),
        background = Color(0xFFFAF3EE),
        onBackground = Color(0xFF332018),
        surface = Color(0xFFFCF7F4).copy(alpha = 0.9f),
        onSurface = Color(0xFF4A3429)
    )
    override val appBackgroundColorEnd: Color = Color(0xFFF4E9E1)
}


object OrangeDarkColorSet : AppColorSet() {
    override val materialColorScheme = darkColorScheme(
        primary = Color(0xFFF59E6C),
        onPrimary = Color(0xFF2B1810),
        secondary = Color(0xFF2B211D),
        onSecondary = Color(0xFFEDE2DC),
        tertiary = Color(0xFF3D2F29),
        onTertiary = Color(0xFFDCCBC2),
        background = Color(0xFF1C1512),
        onBackground = Color(0xFFF3E8E2),
        surface = Color(0xE6261D19),
        onSurface = Color(0xFFEDE2DC)
    )
    override val appBackgroundColorEnd: Color = Color(0xFF15100D)
}
