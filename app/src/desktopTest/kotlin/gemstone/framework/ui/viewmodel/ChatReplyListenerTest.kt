package gemstone.framework.ui.viewmodel

import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.test.Test
import kotlin.test.assertTrue


/**
 * SPEC S2.3 / S2.5 (#191): the reply to a prompt reaches the screen also when the session was opened
 * by selecting a model in the sidebar. The view model used to start collecting the client's events
 * only when a prompt was sent without a session, so after a model selection every streamed token
 * was dropped and the turn stayed at "waiting for the reply".
 */
class ChatReplyListenerTest {
    @Test
    fun theViewModelListensForRepliesBeforeAnyPromptIsSent() {
        ChatViewModel.uiState  // first use of the view model; no prompt, no session
        val listening = runBlocking {
            withTimeoutOrNull(5_000) {
                while (!webSocketClient.hasEventListener) delay(10)
                true
            } ?: false
        }
        assertTrue(listening, "nothing collects the WebSocket client's events, so a streamed reply is lost")
    }
}
