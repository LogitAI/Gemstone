package gemstone.framework.ui.compose.screen.chat

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.foundation.shape.CornerSize
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import gemstone.framework.ui.compose.theme.*
import gemstone.app.generated.resources.*
import gemstone.app.generated.resources.Res
import gemstone.app.generated.resources.bell
import gemstone.app.generated.resources.search
import gemstone.app.generated.resources.sliders
import gemstone.framework.network.ModelInfo
import gemstone.framework.ui.viewmodel.AIModelViewModel
import gemstone.framework.ui.viewmodel.ChatViewModel
import gemstone.framework.ui.viewmodel.SettingsViewModel
import org.jetbrains.compose.resources.stringResource


@Composable
fun SideScreen(
    sideBarMode: Boolean = true,
    onChatSelected: () -> Unit = {}
) {
    val modifier = when (sideBarMode) {
        false -> Modifier.fillMaxSize()
        true -> Modifier.fillMaxHeight().width(Dimen.SIDEBAR_WIDTH)
    }

    LaunchedEffect(Unit) { AIModelViewModel.refreshAIModels() }

    Column(modifier = modifier) {
        Spacer(modifier = Modifier.fillMaxWidth().padding(top = Dimen.LAYOUT_PADDING))

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = Dimen.LAYOUT_PADDING),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            TitleText(stringResource(Res.string.app_title), letterSpacing = (-1).sp, fontSize = 25.sp)
            Spacer(modifier = Modifier.width(Dimen.LIST_ELEMENT_SPACING))
            Row(
                modifier = Modifier,
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically
            ) {
                BlurredFluxIconButton(
                    onClick = {},
                    iconResource = IconResource.Drawable(Res.drawable.bell),
                    iconDescription = stringResource(Res.string.sidebar_notification_section),
                    modifier = Modifier.size(Dimen.BIG_BUTTON_SIZE),
                    shape = MaterialTheme.shapes.large.copy(Dimen.BIG_BUTTON_CORNER_RADIUS),
                    contentPadding = PaddingValues(Dimen.BIG_BUTTON_PADDING)
                )
                Spacer(modifier = Modifier.width(Dimen.LIST_ELEMENT_SPACING))
                PrimaryFluxButton(
                    onClick = { SettingsViewModel.open() },  // the user button opens the settings (S2.7)
                    modifier = Modifier.size(Dimen.BIG_BUTTON_SIZE),
                    shape = MaterialTheme.shapes.large.copy(Dimen.BIG_BUTTON_CORNER_RADIUS)
                ) {
                    SubtitleText(SettingsViewModel.userInitial, fontWeight = FontWeight.ExtraLight, maxLines = 1)
                }
            }
        }

        Spacer(modifier = Modifier.height(Dimen.LAYOUT_PADDING))

//        SettingTitleBar(
//            title = stringResource(Res.string.sidebar_models_section),
//            iconResource = Res.drawable.sliders,
//            iconDescription = stringResource(Res.string.sidebar_models_section_desc),
//            modifier = Modifier.fillMaxWidth().padding(horizontal = Dimen.LAYOUT_PADDING)
//        )

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = Dimen.LAYOUT_PADDING),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            SubtitleText(stringResource(Res.string.sidebar_models_section))
            SecondaryFluxIconButton(
                onClick = {},
                iconResource = IconResource.Drawable(Res.drawable.sliders),
                iconDescription = stringResource(Res.string.sidebar_models_section_desc),
                elevation = ButtonDefaults.elevatedButtonElevation(0.4.dp),
                shape = MaterialTheme.shapes.medium.copy(CornerSize(14.dp)),
                modifier = Modifier,
            )
        }

        LazyRow(
            modifier = Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(horizontal = Dimen.LAYOUT_PADDING),
            horizontalArrangement = Arrangement.spacedBy(Dimen.LIST_ELEMENT_SPACING),
            verticalAlignment = Alignment.CenterVertically
        ) {
            val allEntry = ModelInfo("", "All", "Using Default Model")
            val select = { modelInfo: ModelInfo ->
                if (modelInfo.id.isEmpty()) {
                    AIModelViewModel.deselectAIModel()
                } else {
                    AIModelViewModel.selectAIModel(modelInfo)
                }
            }
            for (modelInfo in listOf(allEntry) + AIModelViewModel.availableAIModels) {
                item(modelInfo) {
                    if (modelInfo.id == AIModelViewModel.selectedAIModel) {
                        PrimaryFluxButton(
                            onClick = { select(modelInfo) },
                            shape = MaterialTheme.shapes.large.copy(Dimen.BIG_BUTTON_CORNER_RADIUS),
                            contentPadding = PaddingValues(Dimen.BIG_BUTTON_PADDING)
                        ) {
                            BodyText(modelInfo.name)
                        }
                    } else {
                        BlurredFluxButton(
                            onClick = { select(modelInfo) },
                            shape = MaterialTheme.shapes.large.copy(Dimen.BIG_BUTTON_CORNER_RADIUS),
                            contentPadding = PaddingValues(Dimen.BIG_BUTTON_PADDING)
                        ) {
                            BodyText(modelInfo.name)
                        }
                    }
                }
            }
            if (AIModelViewModel.modelsUnavailable) {
                item("models-retry") {
                    BlurredFluxButton(
                        onClick = { AIModelViewModel.refreshAIModels() },
                        shape = MaterialTheme.shapes.large.copy(Dimen.BIG_BUTTON_CORNER_RADIUS),
                        contentPadding = PaddingValues(Dimen.BIG_BUTTON_PADDING)
                    ) {
                        BodyText("Server unreachable - retry")
                    }
                }
            }
        }

        LaunchedEffect(Unit) { ChatViewModel.loadSavedChats() }

        // Saved chats, most recent first (SPEC S2.6); the portrait list is reversed, its title at the bottom
        val title = stringResource(Res.string.sidebar_recent_chats_section)
        val newChat = stringResource(Res.string.chat_title_placeholder)
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(Dimen.LAYOUT_PADDING),
            verticalArrangement = Arrangement.spacedBy(Dimen.LIST_ELEMENT_SPACING),
            reverseLayout = !sideBarMode
        ) {
            item("recent-chats-title") {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    SubtitleText(title)
                    SecondaryFluxIconButton(
                        onClick = {},
                        iconResource = IconResource.Drawable(Res.drawable.search),
                        iconDescription = stringResource(Res.string.sidebar_recent_chats_section_desc),
                        elevation = ButtonDefaults.elevatedButtonElevation(0.4.dp),
                        shape = MaterialTheme.shapes.medium.copy(CornerSize(14.dp)),
                        modifier = Modifier,
                    )
                }
            }
            item("new-chat") {
                ChatRow(newChat) {
                    ChatViewModel.reset()
                    onChatSelected()
                }
            }
            items(ChatViewModel.savedChats, key = { it.id }) { chat ->
                ChatRow(chat.title, onDelete = { ChatViewModel.requestDeletion(chat.id) }) {
                    ChatViewModel.openChat(chat.id)
                    onChatSelected()
                }
            }
        }
    }
}


/** One entry of the chat list; [onDelete] adds a delete button. */
@Composable
private fun ChatRow(title: String, onDelete: (() -> Unit)? = null, onOpen: () -> Unit) {
    BlurredFluxButton(
        onClick = onOpen,
        shape = MaterialTheme.shapes.large.copy(Dimen.SURFACE_CORNER_RADIUS),
        clickAnimation = Dimen.SURFACE_CLICK_ANIMATION,
        elevation = Dimen.SURFACE_ELEVATIONS,
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Start,
            verticalAlignment = Alignment.CenterVertically
        ) {
            SecondaryFluxIconButton(
                onClick = onOpen,
                iconResource = IconResource.Drawable(Res.drawable.arrow_up_right),
                iconDescription = "Open This Chat",
                modifier = Modifier,
                shape = MaterialTheme.shapes.extraLarge,
                elevation = ButtonDefaults.elevatedButtonElevation(0.dp)
            )
            SubtitleText(title, fontSize = 16.sp, fontWeight = FontWeight.Normal, maxLines = 1, modifier = Modifier.weight(1f))
            if (onDelete != null) {
                SecondaryFluxIconButton(
                    onClick = onDelete,
                    iconResource = IconResource.Vector(Icons.Default.Delete),
                    iconDescription = stringResource(Res.string.chat_delete_desc),
                    modifier = Modifier,
                    shape = MaterialTheme.shapes.extraLarge,
                    elevation = ButtonDefaults.elevatedButtonElevation(0.dp)
                )
            }
        }
    }
}
