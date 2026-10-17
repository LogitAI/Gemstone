package gemstone.framework.ui.compose.screen.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import gemstone.app.generated.resources.*
import gemstone.framework.network.ServerAddress
import gemstone.framework.network.http.serverSettings
import gemstone.framework.ui.compose.theme.ThemeMode
import gemstone.framework.ui.viewmodel.ChatViewModel
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
                // Light, dark or the system's; the Blue and Orange sets are offered once they have palettes
                Text(stringResource(Res.string.settings_theme))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    for ((mode, label) in listOf(
                        ThemeMode.System to Res.string.settings_theme_system,
                        ThemeMode.Light to Res.string.settings_theme_light,
                        ThemeMode.Dark to Res.string.settings_theme_dark
                    )) {
                        RadioButton(
                            selected = SettingsViewModel.themeInput == mode,
                            onClick = { SettingsViewModel.themeInput = mode }
                        )
                        Text(stringResource(label))
                    }
                }
                SettingsViewModel.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                TextButton(onClick = { ChatViewModel.requestDeletion(ChatViewModel.DELETE_ALL) }) {
                    Text(stringResource(Res.string.settings_delete_all_chats), color = MaterialTheme.colorScheme.error)
                }
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


/** Confirms deleting one saved chat or all of them (SPEC S2.6). */
@Composable
fun DeleteChatsDialog() {
    val pending = ChatViewModel.pendingDeletion ?: return
    val all = pending == ChatViewModel.DELETE_ALL
    AlertDialog(
        onDismissRequest = { ChatViewModel.cancelDeletion() },
        title = { Text(stringResource(if (all) Res.string.chat_delete_all_title else Res.string.chat_delete_title)) },
        text = { Text(stringResource(if (all) Res.string.chat_delete_all_text else Res.string.chat_delete_text)) },
        confirmButton = {
            TextButton(onClick = { ChatViewModel.confirmDeletion() }) {
                Text(stringResource(Res.string.chat_delete_confirm), color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            TextButton(onClick = { ChatViewModel.cancelDeletion() }) { Text(stringResource(Res.string.settings_cancel)) }
        }
    )
}
