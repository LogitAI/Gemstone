package gemstone.framework.ui.viewmodel

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import gemstone.app.generated.resources.Res
import gemstone.app.generated.resources.chat_title_placeholder
import gemstone.framework.network.websocket.ChatEvent
import gemstone.framework.network.websocket.ChatState
import gemstone.framework.storage.SavedChat
import gemstone.framework.storage.chatArchive
import kotlinx.serialization.Serializable
import org.jetbrains.compose.resources.stringResource
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.json.*


@Serializable
enum class ChatRole(val value: String) {
    SYSTEM("system"),
    USER("user"),
    ASSISTANT("assistant"),
    TOOL("tool")
}


@Serializable
data class ChatMessage(
    val role: String,
    val content: String,
    val tool_calls: List<ToolCall>? = null,
    val tool_call_id: String? = null
)


@Serializable
data class ToolCall(
    val id: String,
    val function: ToolFunction
)


@Serializable
data class ToolFunction(
    val name: String,
    val arguments: JsonElement
)


class ChatHistory : MutableList<ChatMessage> {
    private val messages = mutableListOf<ChatMessage>()

    override val size: Int get() = messages.size
    override fun contains(element: ChatMessage): Boolean = messages.contains(element)
    override fun containsAll(elements: Collection<ChatMessage>): Boolean = messages.containsAll(elements)
    override fun get(index: Int): ChatMessage = messages[index]
    override fun indexOf(element: ChatMessage): Int = messages.indexOf(element)
    override fun isEmpty(): Boolean = messages.isEmpty()
    override fun iterator(): MutableIterator<ChatMessage> = messages.iterator()
    override fun lastIndexOf(element: ChatMessage): Int = messages.lastIndexOf(element)
    override fun add(element: ChatMessage): Boolean = messages.add(element)
    override fun add(index: Int, element: ChatMessage) = messages.add(index, element)
    fun add(role: String, content: String = "", tool_calls: List<ToolCall>? = null, tool_call_id: String? = null) {
        messages.add(ChatMessage(role, content, tool_calls, tool_call_id))
    }
    override fun addAll(index: Int, elements: Collection<ChatMessage>): Boolean = messages.addAll(index, elements)
    override fun addAll(elements: Collection<ChatMessage>): Boolean = messages.addAll(elements)
    override fun clear() = messages.clear()
    override fun listIterator(): MutableListIterator<ChatMessage> = messages.listIterator()
    override fun listIterator(index: Int): MutableListIterator<ChatMessage> = messages.listIterator(index)
    override fun remove(element: ChatMessage): Boolean = messages.remove(element)
    override fun removeAll(elements: Collection<ChatMessage>): Boolean = messages.removeAll(elements)
    override fun removeAt(index: Int): ChatMessage = messages.removeAt(index)
    override fun retainAll(elements: Collection<ChatMessage>): Boolean = messages.retainAll(elements)
    override fun set(index: Int, element: ChatMessage): ChatMessage = messages.set(index, element)
    override fun subList(fromIndex: Int, toIndex: Int): MutableList<ChatMessage> = messages.subList(fromIndex, toIndex)
}


@Serializable
data class Conversation(
    val user: String,
    val assistant: String = "",
    val thoughts: String = "",
    val thoughtElapsed: Float = 0f,
    val isThinking: Boolean = false,
    val tools: Map<String, Boolean> = emptyMap()
)


data class ChatUiState(
    val chatId: Int = -1,
    val title: String = "",
    val messages: List<Conversation> = emptyList(),
    val currentMessage: Conversation? = null,
    val messageInput: String = "",
    val isBookmarked: Boolean = false,
    val isResponding: Boolean = true,
    val isConnected: Boolean = false,
    val error: String? = null,
    val failedPrompt: String? = null
) {
    val titleOrPlaceholder: String
        @Composable get() = title.ifEmpty { stringResource(Res.string.chat_title_placeholder) }
}


object ChatViewModel {
    private val chatHistory = ChatHistory()

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private val coroutineScope = CoroutineScope(Dispatchers.Default + SupervisorJob())

    // History size when the turn in flight started: a failed turn drops what it added from here
    private var turnStart = 0
    // The saved chat on screen; null until a new chat's first reply completes (SPEC S2.6)
    private var currentChatId: String? = null

    /** The saved chats for the sidebar, most recently updated first. */
    var savedChats by mutableStateOf(emptyList<SavedChat>())
        private set
    /** The chat search query; null when the search field is closed (SPEC S2.6). */
    var searchQuery by mutableStateOf<String?>(null)

    /** Opens the search field, or closes it and clears the query. */
    fun toggleSearch() {
        searchQuery = if (searchQuery == null) "" else null
    }

    /** A deletion waiting for confirmation: one chat's id, or [DELETE_ALL]. */
    var pendingDeletion by mutableStateOf<String?>(null)
        private set
    const val DELETE_ALL = "*"
    private var observing = false

    fun runBlocking(block: suspend CoroutineScope.() -> Unit) {
        coroutineScope.launch { block() }
    }

    fun initialize() {
        // Called again while no session exists (server down): collecting twice would double every event
        if (observing) return
        observing = true
        observeClientEvents()
        observeClientState()
    }

    fun cleanup() {
        coroutineScope.cancel()
        coroutineScope.launch {
            webSocketClient.deleteSession()
        }
    }

    /** Starts a new, empty chat. */
    fun reset() {
        _uiState.value = ChatUiState()
        chatHistory.clear()
        currentChatId = null
    }

    /** Loads the saved chats for the sidebar. */
    fun loadSavedChats() {
        savedChats = chatArchive.chats
    }

    /** Shows a saved chat and continues it: the next prompt carries its saved history. */
    fun openChat(id: String) {
        val chat = chatArchive.chats.firstOrNull { it.id == id } ?: return
        reset()
        chatHistory.addAll(chat.history)
        currentChatId = chat.id
        _uiState.value = ChatUiState(title = chat.title, messages = chat.turns, isResponding = false)
    }

    /** Asks for confirmation before deleting one chat, or every chat with [DELETE_ALL]. */
    fun requestDeletion(id: String) {
        pendingDeletion = id
    }

    fun cancelDeletion() {
        pendingDeletion = null
    }

    fun confirmDeletion() {
        val id = pendingDeletion ?: return
        pendingDeletion = null
        if (id == DELETE_ALL) chatArchive.deleteAll() else chatArchive.delete(id)
        if (id == DELETE_ALL || id == currentChatId) reset()
        savedChats = chatArchive.chats
    }

    /** Saves the chat on screen after a completed reply (SPEC S2.6). */
    private fun saveCurrentChat() {
        val saved = try {
            chatArchive.save(currentChatId, _uiState.value.messages, chatHistory.toList())
        } catch (e: Exception) {
            println("ERROR: Could not save the chat: ${e.message}")
            null
        } ?: return
        currentChatId = saved.id
        _uiState.update { it.copy(title = saved.title) }
        savedChats = chatArchive.chats
    }

    private fun observeClientEvents() {
        coroutineScope.launch {
            webSocketClient.events.collect { event: ChatEvent ->
                when (event) {
                    is ChatEvent.MessageReceived -> {
                        if (event.isThinking) {
                            _uiState.value = _uiState.value.copy(
                                currentMessage = _uiState.value.currentMessage?.copy(
                                    thoughts = (_uiState.value.currentMessage?.thoughts?.trimStart() ?: "") + event.content
                                )
                            )
                        } else {
                            _uiState.value = _uiState.value.copy(
                                currentMessage = _uiState.value.currentMessage?.copy(
                                    assistant = (_uiState.value.currentMessage?.assistant?.trimStart() ?: "") + event.content
                                )
                            )
                        }
                    }

                    is ChatEvent.ThinkingStarted -> {
                        _uiState.value = _uiState.value.copy(
                            currentMessage = _uiState.value.currentMessage?.copy(
                                isThinking = true
                            )
                        )
                    }

                    is ChatEvent.ThinkingEnded -> {
                        _uiState.value = _uiState.value.copy(
                            currentMessage = _uiState.value.currentMessage?.thoughts?.let {
                                _uiState.value.currentMessage?.copy(
                                    isThinking = false,
                                    thoughts = it.trim()
                                )
                            }
                        )
                    }

                    is ChatEvent.ToolCallReceived -> {
                        val keys = event.toolCall.jsonObject.keys
                        when (val type = keys.firstOrNull()) {
                            "call", "result" -> {
                                val data = event.toolCall.jsonObject[type]?.jsonObject
                                if (data != null) {
                                    val id = data["id"]?.jsonPrimitive?.content ?: ""
                                    val functionName = data["function"]?.jsonObject?.get("name")?.jsonPrimitive?.content ?: ""
                                    val toolKey = "$functionName(): $id"

                                    val currentMsg = _uiState.value.currentMessage
                                    if (currentMsg != null && !currentMsg.tools.containsKey(toolKey)) {
                                        val newTools = currentMsg.tools + (toolKey to false)

                                        _uiState.value = _uiState.value.copy(
                                            currentMessage = currentMsg.copy(tools = newTools)
                                        )
                                    }
                                }
                            }
                            "history" -> {
                                event.toolCall.jsonObject[type]?.jsonArray?.forEach { item ->
                                    val data = item.jsonObject

                                    val role = data["role"]?.jsonPrimitive?.content ?: "error"
                                    val content = data["content"]?.jsonPrimitive?.content ?: ""
                                    val toolCalls = data["tool_calls"]?.jsonArray?.map { toolCall ->
                                        val toolCallObj = toolCall.jsonObject
                                        ToolCall(
                                            id = toolCallObj["id"]?.jsonPrimitive?.content ?: "",
                                            function = ToolFunction(
                                                name = toolCallObj["function"]?.jsonObject?.get("name")?.jsonPrimitive?.content ?: "",
                                                arguments = toolCallObj["function"]?.jsonObject?.get("arguments") ?: JsonNull
                                            )
                                        )
                                    }
                                    val toolCallId = data["tool_call_id"]?.jsonPrimitive?.content
                                    chatHistory.add(role, content, toolCalls, toolCallId)
                                }
                            }
                            else -> {
                                println("WARNING: Unknown event type: $type")
                            }
                        }
                    }

                    is ChatEvent.MessageComplete -> {
                        // Add assistant message to history and UI
                        val data = _uiState.value.currentMessage?.assistant?.trim()
                        if (data.isNullOrEmpty()) {
                            // An empty reply is an error, not a message (SPEC S2.3)
                            failTurn("The server sent an empty reply, please try again.")
                            return@collect
                        }
                        val assistantMessage = data
                        chatHistory.add(ChatRole.ASSISTANT.value, assistantMessage)
                        val currentMessage = Conversation(
                            user = _uiState.value.currentMessage?.user ?: "",
                            assistant = assistantMessage,
                            thoughts = _uiState.value.currentMessage?.thoughts ?: "",
                            thoughtElapsed = _uiState.value.currentMessage?.thoughtElapsed ?: 0f,
                            isThinking = false,
                            tools = _uiState.value.currentMessage?.tools ?: emptyMap()
                        )

                        _uiState.value = _uiState.value.copy(
                            messages = _uiState.value.messages + currentMessage,
                            currentMessage = null,
                            isResponding = false
                        )
                        saveCurrentChat()
                    }

                    is ChatEvent.ErrorOccurred -> failTurn(event.error)
                }
            }
        }
    }

    private fun observeClientState() {
        coroutineScope.launch {
            webSocketClient.state.collect { state ->
                when (state) {
                    is ChatState.Connected -> {
                        _uiState.value = _uiState.value.copy(
                            isConnected = true,
                            error = null
                        )
                    }

                    is ChatState.Disconnected -> {
                        _uiState.value = _uiState.value.copy(
                            isConnected = false
                        )
                    }

                    is ChatState.Thinking -> {
                        _uiState.value = _uiState.value.copy(
                            currentMessage = _uiState.value.currentMessage?.thoughtElapsed?.let {
                                _uiState.value.currentMessage?.copy(
                                    thoughtElapsed = it + state.elapsedSeconds,
                                )
                            }
                        )
                    }

                    is ChatState.Error -> failTurn(state.message)

                    else -> { /* Handle other states */ }
                }
            }
        }
    }

    fun setMessageInput(input: String) {
        _uiState.value = _uiState.value.copy(messageInput = input)
    }

    fun sendMessage() {
        val messageInput = _uiState.value.messageInput.trim()
        if (messageInput.isBlank()) return
        println("INFO: Sending message: $messageInput")

        coroutineScope.launch {
            _uiState.value = _uiState.value.copy(isResponding = true, error = null, failedPrompt = null)

            // Add user message to UI
            val userMessage = Conversation(messageInput)
            _uiState.value = _uiState.value.copy(
                currentMessage = userMessage,
                messageInput = ""
            )

            if (webSocketClient.sessionId == null) {
                run {
                    AIModelViewModel.initializeModel(webSocketClient) {
                        AIModelViewModel.selectedAIModel = "Server Error"
                        AIModelViewModel.selectedAIModelDescription = "Server Not Available"
                    }
                }
                initialize()
            }

            turnStart = chatHistory.size
            val result = webSocketClient.sendMessage(messageInput, chatHistory)
            if (result.isFailure) {
                failTurn("Failed to send message: ${result.exceptionOrNull()?.message}")
            }
        }
    }

    /**
     * Fails the turn in flight (SPEC S2.3, #39): shows [error] and removes the turn and its history
     * records. An error with no turn in flight only shows the error.
     */
    private fun failTurn(error: String) {
        if (_uiState.value.currentMessage != null) chatHistory.dropFrom(turnStart)
        _uiState.update { it.failTurn(error) }
    }

    /** Sends the failed prompt again. */
    fun retry() {
        val retry = _uiState.value.retryInput() ?: return
        _uiState.value = retry
        sendMessage()
    }

    /** Hides the error and puts the failed prompt back in the input. */
    fun clearError() {
        _uiState.update { it.dismissError() }
    }

    fun onCleared() {
        cleanup()
    }
}
