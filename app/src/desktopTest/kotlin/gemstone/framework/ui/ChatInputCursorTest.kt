package gemstone.framework.ui

import androidx.compose.ui.input.key.Key
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.test.requestFocus
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.unit.dp
import gemstone.framework.ui.compose.screen.chat.ChatScreen
import gemstone.framework.ui.compose.theme.GemstoneTheme
import gemstone.framework.ui.viewmodel.AIModelViewModel
import gemstone.framework.ui.viewmodel.ChatViewModel
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals


/** #41: on the desktop, typing after moving the cursor with the arrow keys must insert at the new position. */
@OptIn(ExperimentalTestApi::class)
class ChatInputCursorTest {
    @AfterTest
    fun reset() {
        ChatViewModel.setMessageInput("")
        AIModelViewModel.selectedAIModelDescription = AIModelViewModel.defaultAIModelDescription
    }

    @Test
    fun typingAfterArrowKeysInsertsAtTheNewCursor() = runComposeUiTest {
        setContent { GemstoneTheme { ChatScreen(800.dp) } }
        val input = onNode(hasSetTextAction())
        input.requestFocus()
        input.performTextInput("abc")
        input.performKeyInput {
            pressKey(Key.DirectionLeft)
            pressKey(Key.DirectionLeft)
        }
        input.performTextInput("X")
        assertEquals("aXbc", ChatViewModel.uiState.value.messageInput)
    }

    @Test
    fun aRecompositionBeforeTheTextFlowsBackKeepsTheCursor() = runComposeUiTest {
        // Something else on the screen recomposes it in the frame after a keystroke (here the model
        // description in the header); the field must not see an older text and move the cursor back
        setContent { GemstoneTheme { ChatScreen(800.dp) } }
        val input = onNode(hasSetTextAction())
        input.requestFocus()
        input.performTextInput("abc")
        input.performKeyInput { repeat(2) { pressKey(Key.DirectionLeft) } }
        mainClock.autoAdvance = false
        input.performTextInput("X")
        AIModelViewModel.selectedAIModelDescription = "Recomposed"
        mainClock.advanceTimeByFrame()
        mainClock.autoAdvance = true
        input.performTextInput("Y")
        assertEquals("aXYbc", ChatViewModel.uiState.value.messageInput)
    }

    @Test
    fun typingCharacterByCharacterAfterArrowKeysKeepsTheCursor() = runComposeUiTest {
        setContent { GemstoneTheme { ChatScreen(800.dp) } }
        val input = onNode(hasSetTextAction())
        input.requestFocus()
        for (c in "hello") input.performTextInput(c.toString())
        input.performKeyInput { repeat(3) { pressKey(Key.DirectionLeft) } }
        for (c in "XY") input.performTextInput(c.toString())
        assertEquals("heXYllo", ChatViewModel.uiState.value.messageInput)
    }
}
