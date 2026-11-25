package gemstone.framework.network.websocket

import gemstone.framework.network.ChatStreamDecoder
import gemstone.framework.network.ChatTransport
import gemstone.framework.network.appHttpClient
import gemstone.framework.network.ServerAddress
import gemstone.framework.network.authorize
import gemstone.framework.network.http.serverSettings
import gemstone.framework.ui.viewmodel.ChatHistory
import gemstone.framework.ui.viewmodel.ChatRole
import io.ktor.client.plugins.websocket.*
import io.ktor.client.request.*
import io.ktor.client.statement.*
import io.ktor.http.*
import io.ktor.websocket.*
import kotlinx.coroutines.flow.*
import kotlin.time.Clock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement


sealed class ChatState {
    data object Disconnected : ChatState()
    data object Connecting : ChatState()
    data object Connected : ChatState()
    data class Thinking(val elapsedSeconds: Float) : ChatState()
    data object Responding : ChatState()
    data class Error(val message: String) : ChatState()
}


sealed class ChatEvent {
    data class MessageReceived(val content: String, val isThinking: Boolean = false) : ChatEvent()
    data object ThinkingStarted : ChatEvent()
    data object ThinkingEnded : ChatEvent()
    data class ToolCallReceived(val toolCall: JsonElement) : ChatEvent()
    data object MessageComplete : ChatEvent()
    data class ErrorOccurred(val error: String) : ChatEvent()
}


/** Close codes after which the reply is complete: 1000 (normal) and 1005 (no status code sent). */
private val NORMAL_CLOSE_CODES = setOf(1000, 1005)

/**
 * What a server close frame means for the chat (SPEC S1.4). A normal close ends the reply; any
 * other code (1008 unknown model, 1011 offline or failed download, 1013 busy) is an error that
 * carries the server's reason, so the app shows it instead of treating the close as a finished
 * reply. Pure, so it is tested without a socket.
 */
fun closeEventFor(code: Int?, reason: String?): ChatEvent {
    if (code == null || code in NORMAL_CLOSE_CODES) return ChatEvent.MessageComplete
    val text = reason?.trim().orEmpty()
    return ChatEvent.ErrorOccurred(text.ifEmpty { "The server closed the connection (code $code)." })
}


@Serializable
data class SessionResponse(
    val model_id: String,
    val session_id: String,
    val message: String
)


class ChatWebSocketClient(
    // Read on every request, so a change saved in the settings applies at once (SPEC S2.7)
    private val address: () -> ServerAddress = { serverSettings.address },
    private val apiKey: () -> String? = { serverSettings.apiKey }
) : ChatTransport {
    private val httpClient = appHttpClient()

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }

    private var webSocketSession: DefaultClientWebSocketSession? = null
    override var sessionId: String? = null
        set(value) {
            field = value
            if (value != null) {
                println("INFO: Session ID set to $value")
            } else {
                println("INFO: Session ID cleared")
            }
        }

    private val _events = MutableSharedFlow<ChatEvent>()
    override val events: SharedFlow<ChatEvent> = _events.asSharedFlow()

    /** True while something collects [events]; an event emitted with no collector is lost. */
    internal val hasEventListener: Boolean
        get() = _events.subscriptionCount.value > 0

    private val _state = MutableStateFlow<ChatState>(ChatState.Disconnected)
    override val state: StateFlow<ChatState> = _state.asStateFlow()

    // The server's stream protocol, shared with the in-app transport (SPEC S3.1); a reply ends with a disconnect
    private val decoder = ChatStreamDecoder(_events, _state) { disconnect() }

    override suspend fun createSession(modelId: String): Result<String> {
        return try {
            val response = httpClient.post(address().httpUrl("/api/models/$modelId/sessions/")) { authorize(apiKey()) }
            if (response.status == HttpStatusCode.OK) {
                val sessionResponse = json.decodeFromString<SessionResponse>(response.bodyAsText())
                sessionId = sessionResponse.session_id
                Result.success(sessionResponse.session_id)
            } else {
                Result.failure(Exception("Failed to create session: ${response.status}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    private suspend fun connect(): Result<Unit> {
        return try {
            _state.value = ChatState.Connecting
            webSocketSession = httpClient.webSocketSession(address().wsUrl("/api/chat/streaming")) { authorize(apiKey()) }
            _state.value = ChatState.Connected
            Result.success(Unit)
        } catch (e: Exception) {
            _state.value = ChatState.Error(e.message ?: "Connection failed")
            Result.failure(e)
        }
    }

    override suspend fun sendMessage(
        message: String,
        chatHistory: ChatHistory
    ): Result<Unit> {
        return try {
            // Connect to WebSocket
            val connectResult = connect()
            if (connectResult.isFailure) {
                println("ERROR: Failed to connect WebSocket: ${connectResult.exceptionOrNull()?.message}")
                return connectResult
            }
            println("INFO: WebSocket connected successfully")

            val session = webSocketSession ?: return Result.failure(Exception("Not connected"))
            val currentSessionId = sessionId ?: return Result.failure(Exception("No session ID"))

            // Send session ID
            session.send(json.encodeToString(mapOf("session_id" to currentSessionId)))

            // Send chat history
            session.send(json.encodeToString(chatHistory.toList()))

            // Send user message
            session.send(message)

            // Add user message to history
            chatHistory.add(ChatRole.USER.value, message)

            // Start listening for responses
            listenForMessages()

            // Disconnect after sending the message
            disconnect()
            println("INFO: Message sent and WebSocket disconnected successfully")

            Result.success(Unit)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    private suspend fun listenForMessages() {
        val session = webSocketSession ?: return

        try {
            for (frame in session.incoming) {
                when (frame) {
                    is Frame.Text -> {
                        val message = frame.readText()
                        handleMessage(message)
                    }
                    is Frame.Close -> {
                        val closeReason = frame.readReason()
                        val event = closeEventFor(closeReason?.code?.toInt(), closeReason?.message)
                        _state.value = if (event is ChatEvent.ErrorOccurred) {
                            ChatState.Error(event.error)
                        } else {
                            ChatState.Disconnected
                        }
                        _events.emit(event)
                        break
                    }
                    else -> { /* Handle other frame types if needed */ }
                }
            }
        } catch (e: Exception) {
            _state.value = ChatState.Error(e.message ?: "Connection error")
            _events.emit(ChatEvent.ErrorOccurred(e.message ?: "Connection error"))
        }
    }

    private suspend fun handleMessage(message: String) {
        println("INFO: Received message: $message")
        decoder.accept(message)
    }

    private suspend fun disconnect() {
        try {
            webSocketSession?.close()
            webSocketSession = null
            _state.value = ChatState.Disconnected
        } catch (e: Exception) {
            _state.value = ChatState.Error("Disconnect failed: ${e.message}")
        }
    }

    override suspend fun deleteSession() {
        val currentSessionId = sessionId ?: return
        try {
            httpClient.delete(address().httpUrl("/api/sessions/$currentSessionId")) { authorize(apiKey()) }
            sessionId = null
        } catch (e: Exception) {
            println("Failed to delete session: ${e.message}")
        }
    }

    /** Forgets the session without telling the server: it belongs to a server no longer in use (S2.7). */
    override fun forgetSession() {
        sessionId = null
    }

    override fun close() {
        httpClient.close()
    }
}
