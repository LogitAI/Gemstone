package gemstone.framework.ui

import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.runComposeUiTest
import gemstone.framework.ui.compose.screen.chat.MessageBubble
import gemstone.framework.ui.compose.theme.GemstoneTheme
import gemstone.framework.ui.viewmodel.ChatRole
import kotlin.test.Test
import kotlin.test.assertTrue


/** SPEC S2.9 (#261): the reasoning status line above a reply is an English string resource. */
@OptIn(ExperimentalTestApi::class)
class ReasoningStatusTest {
    private fun status(seconds: Int, thinking: Boolean, expected: String) = runComposeUiTest {
        setContent {
            GemstoneTheme { MessageBubble(ChatRole.ASSISTANT, "Hello", Triple("weighing", seconds, thinking)) }
        }
        onNodeWithText(expected, substring = true).assertExists()
        val texts = onAllNodes(SemanticsMatcher("any node") { true }, useUnmergedTree = true)
            .fetchSemanticsNodes()
            .flatMap { it.config.getOrNull(SemanticsProperties.Text).orEmpty() }
            .joinToString(" ") { it.text }
        assertTrue(texts.none { it in '가'..'힣' }, "Hangul left in the reply: $texts")
    }

    @Test
    fun whileThinking() = status(3, thinking = true, expected = "Thinking for 3 s...")

    @Test
    fun afterThinking() = status(3, thinking = false, expected = "Thought for 3 s")

    @Test
    fun beforeTheFirstSecond() = status(0, thinking = true, expected = "Waiting for the reply...")
}
