package gemstone.framework.ui.compose.screen.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import gemstone.app.generated.resources.*
import gemstone.framework.network.ServerAddress
import gemstone.framework.network.http.serverSettings
import gemstone.framework.ui.viewmodel.SettingsViewModel
import org.jetbrains.compose.resources.stringResource


/** The settings dialog (SPEC S2.7): the server address and the API key. */
@Composable
fun SettingsDialog() {
    if (!SettingsViewModel.dialogOpen) return
    val settings = serverSettings
    val setAtLaunch = stringResource(Res.string.settings_set_at_launch)

    AlertDialog(
        onDismissRequest = { SettingsViewModel.cancel() },
        title = { Text(stringResource(Res.string.settings_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = SettingsViewModel.addressInput,
                    onValueChange = { SettingsViewModel.addressInput = it },
                    label = { Text(stringResource(Res.string.settings_server_address)) },
                    placeholder = { Text("${ServerAddress.DEFAULT_HOST}:${ServerAddress.DEFAULT_PORT}") },
                    supportingText = if (settings.addressSetAtLaunch) ({ Text(setAtLaunch) }) else null,
                    enabled = !settings.addressSetAtLaunch,
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                )
                // The web client sends no key (S2.2), so it has no key field
                if (settings.editable) {
                    OutlinedTextField(
                        value = SettingsViewModel.apiKeyInput,
                        onValueChange = { SettingsViewModel.apiKeyInput = it },
                        label = { Text(stringResource(Res.string.settings_api_key)) },
                        supportingText = when {
                            settings.keySetAtLaunch -> ({ Text(setAtLaunch) })
                            !settings.keyPersistent -> ({ Text(stringResource(Res.string.settings_key_this_run)) })
                            else -> null
                        },
                        enabled = !settings.keySetAtLaunch,
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password)
                    )
                }
                SettingsViewModel.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(onClick = { SettingsViewModel.save() }) { Text(stringResource(Res.string.settings_save)) }
        },
        dismissButton = {
            TextButton(onClick = { SettingsViewModel.cancel() }) { Text(stringResource(Res.string.settings_cancel)) }
        }
    )
}
