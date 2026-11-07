package gemstone.framework.ui.viewmodel

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import gemstone.framework.network.http.serverSettings
import gemstone.framework.ui.compose.theme.AppColorSet.Companion.ColorSet
import gemstone.framework.ui.compose.theme.ThemeMode
import gemstone.framework.ui.compose.theme.themeSettings


object SettingsViewModel {
    var userInitial by mutableStateOf("CU")
    var userFirstName by mutableStateOf("Chaeun")

    // The settings dialog (SPEC S2.7)
    var dialogOpen by mutableStateOf(false)
        private set
    var addressInput by mutableStateOf("")
    var apiKeyInput by mutableStateOf("")
    var themeInput by mutableStateOf(ThemeMode.System)
    var colorSetInput by mutableStateOf(ColorSet.Black)
    var error by mutableStateOf<String?>(null)
        private set

    fun open() {
        addressInput = serverSettings.addressText
        apiKeyInput = if (serverSettings.keySetAtLaunch) "" else serverSettings.apiKey.orEmpty()
        themeInput = themeSettings.mode
        colorSetInput = themeSettings.colorSet
        error = null
        dialogOpen = true
    }

    /** Applies and persists a theme mode choice immediately (SPEC S2.7). */
    fun selectThemeMode(mode: ThemeMode) {
        themeInput = mode
        themeSettings.save(mode, colorSetInput)
    }

    /** Applies and persists a color set choice immediately (SPEC S2.7). */
    fun selectColorSet(colorSet: ColorSet) {
        colorSetInput = colorSet
        themeSettings.save(themeInput, colorSet)
    }

    fun cancel() {
        dialogOpen = false
    }

    /** Saves and closes, or keeps the dialog open with the error. A change applies at once. */
    fun save() {
        val before = serverSettings.address to serverSettings.apiKey
        error = serverSettings.save(addressInput, apiKeyInput)
        if (error != null) return
        themeSettings.save(themeInput, colorSetInput)
        dialogOpen = false
        if (before != serverSettings.address to serverSettings.apiKey) {
            // The session belongs to the old server; the model list comes from the new one
            webSocketClient.forgetSession()
            AIModelViewModel.refreshAIModels()
        }
    }
}
