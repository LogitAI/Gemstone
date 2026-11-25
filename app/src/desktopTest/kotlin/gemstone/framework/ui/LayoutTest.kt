package gemstone.framework.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.ComposeUiTest
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.unit.dp
import gemstone.framework.network.ChatTransports
import gemstone.framework.network.InAppChatTransport
import gemstone.framework.network.InAppEngine
import gemstone.framework.network.ModelInfo
import gemstone.framework.ui.compose.screen.chat.MainScreen
import gemstone.framework.ui.compose.theme.GemstoneTheme
import gemstone.framework.ui.viewmodel.ChatMessage
import gemstone.framework.ui.viewmodel.ChatViewModel
import gemstone.framework.ui.viewmodel.webSocketClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.flow.emptyFlow
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test


/**
 * SPEC S2.4 (#261, #263): in landscape the sidebar sits next to the chat; in portrait the sidebar is a start
 * screen, choosing a chat navigates to the chat, and a swipe from the left edge returns to the sidebar. The screen runs on an in-app transport with no
 * models, so the sidebar's model list opens no connection (InAppChatTest counts them).
 */
@OptIn(ExperimentalTestApi::class, ExperimentalCoroutinesApi::class)
class LayoutTest {
    private object NoModels : InAppEngine {
        override suspend fun models(): List<ModelInfo> = emptyList()
        override fun chat(modelId: String, history: List<ChatMessage>, message: String): Flow<String> = emptyFlow()
    }

    /** A resumed host, as the app's window is: the navigation entries follow its lifecycle. */
    private object Resumed : LifecycleOwner {
        override val lifecycle = LifecycleRegistry.createUnsafe(this).apply { currentState = Lifecycle.State.RESUMED }
    }

    @Composable
    private fun Screen(width: Int, height: Int) {
        CompositionLocalProvider(LocalLifecycleOwner provides Resumed) {
            GemstoneTheme { Box(Modifier.size(width.dp, height.dp)) { MainScreen() } }
        }
    }

    @BeforeTest
    fun offline() {
        ChatTransports.active = InAppChatTransport(NoModels)
        // The lifecycle library accepts state changes only on the main dispatcher's thread; the test's
        // composition runs on its own thread, so that thread is made the main one for the test.
        Dispatchers.setMain(Dispatchers.Unconfined)
    }

    @AfterTest
    fun reset() {
        Dispatchers.resetMain()
        ChatViewModel.reset()
        ChatTransports.active = webSocketClient
    }

    @Test
    fun landscapeShowsTheSidebarBesideTheChat() = runComposeUiTest {
        setContent { Screen(1200, 800) }
        waitForIdle()
        onNodeWithText("Recent Chats").assertExists()
        onNode(hasSetTextAction()).assertExists()  // the chat's input field
    }

    @Test
    fun portraitStartsOnTheSidebarAndNavigatesToTheChat() = runComposeUiTest {
        setContent { Screen(400, 800) }
        waitForIdle()
        onNodeWithText("Recent Chats").assertExists()
        onNode(hasSetTextAction()).assertDoesNotExist()

        onNodeWithText("New Chat").performClick()
        mainClock.advanceTimeBy(1_000)  // past the 250 ms slide, so the chat entry is settled
        waitForIdle()
        onNode(hasSetTextAction()).assertExists()
        onNodeWithText("Recent Chats").assertDoesNotExist()
    }

    /** Portrait, on the chat: opened from the sidebar's "New Chat". */
    private fun ComposeUiTest.openChatInPortrait() {
        setContent { Screen(400, 800) }
        waitForIdle()
        onNodeWithText("New Chat").performClick()
        mainClock.advanceTimeBy(1_000)
        waitForIdle()
        onNode(hasSetTextAction()).assertExists()
    }

    /** Drags from [startX] to [endX] (fractions of the width) at mid-height, in small steps like a finger. */
    private fun ComposeUiTest.drag(startX: Float, endX: Float) {
        onRoot().performTouchInput {
            val y = height / 2f
            down(Offset(width * startX, y))
            val steps = 20
            for (i in 1..steps) moveTo(Offset(width * (startX + (endX - startX) * i / steps), y))
            up()
        }
        mainClock.advanceTimeBy(1_000)
        waitForIdle()
    }

    @Test
    fun portraitSwipeFromTheLeftEdgeReturnsToTheSidebar() = runComposeUiTest {
        openChatInPortrait()
        drag(startX = 0.02f, endX = 0.9f)
        onNodeWithText("Recent Chats").assertExists()
        onNode(hasSetTextAction()).assertDoesNotExist()
    }

    @Test
    fun portraitShortSwipeStaysOnTheChat() = runComposeUiTest {
        openChatInPortrait()
        drag(startX = 0.02f, endX = 0.2f)  // below the 40 % threshold
        onNode(hasSetTextAction()).assertExists()
        onNodeWithText("Recent Chats").assertDoesNotExist()
    }

    @Test
    fun portraitSwipeAwayFromTheEdgeStaysOnTheChat() = runComposeUiTest {
        openChatInPortrait()
        drag(startX = 0.6f, endX = 0.98f)  // starts outside the edge area
        onNode(hasSetTextAction()).assertExists()
    }
}
