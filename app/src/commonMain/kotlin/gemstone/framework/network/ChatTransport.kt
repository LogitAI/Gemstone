package gemstone.framework.network

import gemstone.framework.network.http.HttpClientFactory
import gemstone.framework.network.websocket.ChatEvent
import gemstone.framework.network.websocket.ChatState
import gemstone.framework.network.websocket.ChatWebSocketClient
import gemstone.framework.ui.viewmodel.ChatHistory
import gemstone.framework.ui.viewmodel.ChatMessage
import gemstone.framework.ui.viewmodel.ChatRole
import io.ktor.client.HttpClient
import io.ktor.client.plugins.HttpSend
import io.ktor.client.plugins.plugin
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlin.time.Clock


/**
 * Where a chat runs (SPEC S3.1): the server over HTTP and a WebSocket ([ChatWebSocketClient]), or an
 * engine inside the app ([InAppChatTransport]). The chat screen, the model selection and the settings
 * use whichever [ChatTransports.active] is.
 */
interface ChatTransport {
    val events: SharedFlow<ChatEvent>
    val state: StateFlow<ChatState>
    val sessionId: String?

    suspend fun createSession(modelId: String): Result<String>

    /** Adds [message] to [chatHistory] and streams the reply as [events]. */
    suspend fun sendMessage(message: String, chatHistory: ChatHistory): Result<Unit>

    suspend fun deleteSession()

    /** Forgets the session without telling anyone: it belongs to a backend no longer in use (S2.7). */
    fun forgetSession()

    fun close()
}


/** The transport the app uses now; the view models follow [activeFlow] when it changes. */
object ChatTransports {
    /** The server transport (S1.4), the default. */
    val server: ChatWebSocketClient by lazy { ChatWebSocketClient() }

    private val _active: MutableStateFlow<ChatTransport> by lazy { MutableStateFlow(server) }

    val activeFlow: StateFlow<ChatTransport> get() = _active

    var active: ChatTransport
        get() = _active.value
        set(value) {
            _active.value = value
        }
}


/**
 * A model engine inside the app (SPEC S3.1): the models stored on the device, and a reply streamed in the
 * server's WebSocket protocol (text chunks, `<think>`, `</think>`, `<tool_call>...</tool_call>`, `<EOS>`).
 */
interface InAppEngine {
    suspend fun models(): List<ModelInfo>

    /** The reply to [message] after [history], one protocol message per element, ending with `<EOS>`. */
    fun chat(modelId: String, history: List<ChatMessage>, message: String): Flow<String>
}


/** Chat through an [InAppEngine]: no network at all (SPEC S3.2). */
class InAppChatTransport(private val engine: InAppEngine) : ChatTransport {
    private val _events = MutableSharedFlow<ChatEvent>()
    override val events: SharedFlow<ChatEvent> = _events.asSharedFlow()

    private val _state = MutableStateFlow<ChatState>(ChatState.Disconnected)
    override val state: StateFlow<ChatState> = _state.asStateFlow()

    private val decoder = ChatStreamDecoder(_events, _state)
    private var modelId: String? = null

    override var sessionId: String? = null
        private set

    /** The models stored on the device. */
    suspend fun models(): List<ModelInfo> = engine.models()

    override suspend fun createSession(modelId: String): Result<String> {
        this.modelId = modelId
        return Result.success("in-app:$modelId".also { sessionId = it })
    }

    override suspend fun sendMessage(message: String, chatHistory: ChatHistory): Result<Unit> {
        val model = modelId ?: return Result.failure(IllegalStateException("No model selected"))
        val history = chatHistory.toList()
        chatHistory.add(ChatRole.USER.value, message)
        _state.value = ChatState.Connected
        // An event emitted before the chat screen collects is lost; wait for it as the server path does
        withTimeoutOrNull(5_000) { _events.subscriptionCount.first { it > 0 } }
        return try {
            engine.chat(model, history, message).collect { decoder.accept(it) }
            _state.value = ChatState.Disconnected
            Result.success(Unit)
        } catch (e: Exception) {
            _state.value = ChatState.Error(e.message ?: "The in-app engine failed")
            Result.failure(e)
        }
    }

    override suspend fun deleteSession() = forgetSession()

    override fun forgetSession() {
        sessionId = null
        modelId = null
    }

    override fun close() {}
}


/**
 * Turns the server's stream protocol (SPEC S1.4) into chat events, for both transports: `<think>` and
 * `</think>` bracket reasoning, `<tool_call>...</tool_call>` carries tool calls, `<EOS>` ends the reply.
 */
class ChatStreamDecoder(
    private val events: MutableSharedFlow<ChatEvent>,
    private val state: MutableStateFlow<ChatState>,
    private val beforeComplete: suspend () -> Unit = {},
) {
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }
    private var thinkingStartTime: Long = 0
    private var isThinking = false

    suspend fun accept(message: String) {
        when {
            message == "<EOS>" -> {
                beforeComplete()
                events.emit(ChatEvent.MessageComplete)
            }

            message == "<think>" -> {
                isThinking = true
                thinkingStartTime = Clock.System.now().toEpochMilliseconds()
                state.value = ChatState.Thinking(0f)
                events.emit(ChatEvent.ThinkingStarted)
            }

            message == "</think>" -> {
                isThinking = false
                state.value = ChatState.Responding
                events.emit(ChatEvent.ThinkingEnded)
            }

            message.contains("<tool_call>") -> {
                val regex = """<tool_call>([\s\S]*?)</tool_call>""".toRegex()
                regex.findAll(message).forEach { match -> toolCall(match.groupValues[1]) }
            }

            else -> {
                if (isThinking) {
                    events.emit(ChatEvent.MessageReceived(message, isThinking = true))
                    val currentTime = Clock.System.now().toEpochMilliseconds()
                    val elapsedSeconds = (currentTime - thinkingStartTime) / 1000f
                    thinkingStartTime = currentTime
                    state.value = ChatState.Thinking(elapsedSeconds)
                } else {
                    state.value = ChatState.Responding
                    events.emit(ChatEvent.MessageReceived(message, isThinking = false))
                }
            }
        }
    }

    private suspend fun toolCall(message: String) {
        try {
            events.emit(ChatEvent.ToolCallReceived(json.decodeFromString<JsonElement>(message.trim())))
        } catch (e: Exception) {
            // Skipped, not an error: the reply continues, and an error would fail the turn (SPEC S2.3)
            println("WARNING: Skipping a tool call that cannot be parsed: ${e.message}")
        }
    }
}


/** Every request the app's HTTP client sends, HTTP or a WebSocket upgrade (SPEC S3.2). */
object NetworkActivity {
    private val count = MutableStateFlow(0)

    val requests: Int get() = count.value

    internal fun record() = count.update { it + 1 }
}


/** The app's HTTP client: the platform's, with every request counted in [NetworkActivity]. */
fun appHttpClient(): HttpClient = HttpClientFactory.create().also { client ->
    client.plugin(HttpSend).intercept { request ->
        NetworkActivity.record()
        execute(request)
    }
}
