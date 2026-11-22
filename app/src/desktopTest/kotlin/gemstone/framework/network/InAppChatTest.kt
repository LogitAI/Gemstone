package gemstone.framework.network

import gemstone.framework.ui.viewmodel.AIModelViewModel
import gemstone.framework.ui.viewmodel.ChatMessage
import gemstone.framework.ui.viewmodel.ChatViewModel
import gemstone.framework.ui.viewmodel.webSocketClient
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail


/**
 * SPEC S3.1 / S3.2 (#42, #190): with the in-app transport active, listing the models, selecting one and
 * completing a chat send no request; the same steps on the server transport do, so the counter is seen
 * to observe them. The engine here is a fake; the real one runs Gemstone's Python engine in the app.
 */
class InAppChatTest {
    private class FakeEngine : InAppEngine {
        val prompts = mutableListOf<String>()
        val models = mutableListOf<String>()

        override suspend fun models(): List<ModelInfo> =
            listOf(ModelInfo(id = "on-device/tiny", name = "Tiny (on device)", description = "a model stored on the device"))

        override fun chat(modelId: String, history: List<ChatMessage>, message: String): Flow<String> = flow {
            models += modelId
            prompts += message
            for (chunk in listOf("<think>", "weighing", "</think>", "Hello", " offline", "<EOS>")) emit(chunk)
        }
    }

    @AfterTest
    fun reset() {
        // Deselecting opens a session on the active transport from a launched coroutine. Done on the server
        // transport, that request could land inside the next test's "no request" window; so it is done on
        // an in-app transport, and the server comes back only after that session is open.
        val quiet = InAppChatTransport(FakeEngine())
        ChatTransports.active = quiet
        ChatViewModel.reset()
        AIModelViewModel.deselectAIModel()
        waitFor("the teardown session") { quiet.sessionId != null }
        ChatTransports.active = webSocketClient
    }

    private fun waitFor(what: String, condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 10_000
        while (!condition()) {
            if (System.currentTimeMillis() > deadline) fail("timed out waiting for $what")
            Thread.sleep(20)
        }
    }

    @Test
    fun aChatOnTheInAppEngineOpensNoConnection() {
        val engine = FakeEngine()
        ChatTransports.active = InAppChatTransport(engine)
        val before = NetworkActivity.requests

        AIModelViewModel.refreshAIModels()  // launched on the view model's scope, not awaited
        waitFor("the in-app model list") { AIModelViewModel.availableAIModels.isNotEmpty() }
        assertEquals(listOf("on-device/tiny"), AIModelViewModel.availableAIModels.map { it.id })
        AIModelViewModel.selectAIModel(AIModelViewModel.availableAIModels.single())

        ChatViewModel.setMessageInput("Hi")
        ChatViewModel.sendMessage()
        waitFor("the reply") { ChatViewModel.uiState.value.messages.isNotEmpty() }

        val turn = ChatViewModel.uiState.value.messages.last()
        assertEquals("Hi", turn.user)
        assertEquals("Hello offline", turn.assistant)
        assertEquals("weighing", turn.thoughts)
        assertEquals(listOf("Hi"), engine.prompts)
        assertEquals(listOf("on-device/tiny"), engine.models)
        assertEquals(before, NetworkActivity.requests, "the in-app transport opened a connection")
    }

    @Test
    fun theServerTransportIsCounted() {
        // The other half of the check: without it a counter that never moves would pass the test above.
        ChatTransports.active = webSocketClient
        val before = NetworkActivity.requests
        AIModelViewModel.refreshAIModels()  // whether or not a server answers, a request is sent
        val deadline = System.currentTimeMillis() + 10_000
        while (NetworkActivity.requests == before && System.currentTimeMillis() < deadline) Thread.sleep(20)
        assertTrue(NetworkActivity.requests > before, "a model-list request on the server transport was not counted")
    }
}
