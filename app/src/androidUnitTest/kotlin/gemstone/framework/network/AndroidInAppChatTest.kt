package gemstone.framework.network

import gemstone.framework.network.settings.AndroidSettings
import gemstone.framework.ui.viewmodel.AIModelViewModel
import gemstone.framework.ui.viewmodel.ChatMessage
import gemstone.framework.ui.viewmodel.ChatViewModel
import gemstone.framework.ui.viewmodel.webSocketClient
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import org.junit.FixMethodOrder
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import java.io.File
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail


/**
 * SPEC S3.2 (#190), the Android half of `InAppChatTest` (desktopTest): the same chat on the in-app
 * transport, run on Android's own framework under Robolectric. The model list, selection and reply go
 * through the Android source set (`SharedPreferences` for the settings, `AtomicFile` for `chats.json` in
 * the app's files directory), and no request is counted; the server transport's request is.
 *
 * The test order is fixed: the chat archive and the view models are process-wide objects that keep the
 * files directory of the first test's application, and the in-app test reads `chats.json` from it.
 */
@RunWith(RobolectricTestRunner::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class AndroidInAppChatTest {
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

    @BeforeTest
    fun context() {
        AndroidSettings.init(RuntimeEnvironment.getApplication())  // what MainActivity does
    }

    @AfterTest
    fun reset() {
        // As in InAppChatTest: deselect on an in-app transport, so the teardown's session request cannot
        // land in the next test's "no request" window, and wait for it only when a model was selected.
        val quiet = InAppChatTransport(FakeEngine())
        ChatTransports.active = quiet
        ChatViewModel.reset()
        val opensSession = AIModelViewModel.selectedAIModel.isNotEmpty()
        AIModelViewModel.deselectAIModel()
        if (opensSession) waitFor("the teardown session") { quiet.sessionId != null }
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

        // The completed chat is saved through Android's AtomicFile in the app's private files directory
        val chats = File(AndroidSettings.context.filesDir, "chats.json")
        waitFor("chats.json") { chats.exists() && "Hello offline" in chats.readText() }

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
