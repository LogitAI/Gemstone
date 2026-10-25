package gemstone.framework.ui

import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.platform.ClipEntry
import androidx.compose.ui.platform.Clipboard
import androidx.compose.ui.platform.LocalClipboard
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.performMouseInput
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.test.withKeyDown
import gemstone.framework.ui.compose.screen.chat.MessageBubble
import gemstone.framework.ui.compose.theme.GemstoneTheme
import gemstone.framework.ui.viewmodel.ChatRole
import androidx.compose.ui.text.AnnotatedString
import java.awt.datatransfer.DataFlavor
import java.awt.datatransfer.Transferable
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue


/** #202: message text can be selected with the mouse and copied. */
@OptIn(ExperimentalTestApi::class, ExperimentalComposeUiApi::class)
class MessageSelectionTest {
    private class FakeClipboard : Clipboard {
        var entry: ClipEntry? = null
        override suspend fun getClipEntry(): ClipEntry? = entry
        override suspend fun setClipEntry(clipEntry: ClipEntry?) {
            entry = clipEntry
        }
        override val nativeClipboard: Any get() = this
        val text: String?
            get() = when (val native = entry?.nativeClipEntry) {
                is AnnotatedString -> native.text
                is String -> native
                is Transferable -> native.getTransferData(DataFlavor.stringFlavor) as? String
                else -> null
            }
    }

    private val copyModifier = if ("mac" in System.getProperty("os.name").lowercase()) Key.MetaLeft else Key.CtrlLeft

    /** Drags across the [role] bubble showing [text] and copies; the copy must be part of [rendered]. */
    private fun copiesSelection(role: ChatRole, text: String, rendered: String = text) = runComposeUiTest {
        val clipboard = FakeClipboard()
        setContent {
            CompositionLocalProvider(LocalClipboard provides clipboard) {
                GemstoneTheme { MessageBubble(role, text) }
            }
        }
        onNodeWithText(rendered.substringBefore(' '), substring = true, useUnmergedTree = true).performMouseInput {
            moveTo(centerLeft)
            press()
            moveTo(centerRight)
            release()
        }
        onRoot().performKeyInput { withKeyDown(copyModifier) { pressKey(Key.C) } }
        waitForIdle()
        val copied = clipboard.text
        assertNotNull(copied, "nothing was copied")
        assertTrue(copied.isNotBlank() && copied in rendered, "copied '$copied' is not part of '$rendered'")
    }

    @Test
    fun aReplyCanBeSelectedAndCopied() = copiesSelection(ChatRole.ASSISTANT, "Gemstone runs models locally")

    @Test
    fun aPromptCanBeSelectedAndCopied() = copiesSelection(ChatRole.USER, "What is Gemstone?")

    @Test
    fun markdownStyledTextIsCopiedWithoutItsMarkers() =
        copiesSelection(ChatRole.ASSISTANT, "Use **uv** to run it", rendered = "Use uv to run it")
}
