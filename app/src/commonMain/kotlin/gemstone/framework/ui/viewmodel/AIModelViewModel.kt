package gemstone.framework.ui.viewmodel

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import gemstone.framework.network.ModelCatalog
import gemstone.framework.network.ModelInfo
import gemstone.framework.network.http.ModelsApi
import gemstone.framework.network.websocket.ChatWebSocketClient


val webSocketClient = ChatWebSocketClient()


object AIModelViewModel {
    /** Server alias used when no model list is known (server unreachable). */
    private const val FALLBACK_MODEL_ID = ModelCatalog.DEFAULT_ALIAS
    private const val FALLBACK_DESCRIPTION = "Default model"

    private val modelsApi = ModelsApi()

    /** Id sent when no model is selected; set from the server's `default` alias. */
    var defaultAIModel by mutableStateOf(FALLBACK_MODEL_ID)
    var defaultAIModelDescription by mutableStateOf(FALLBACK_DESCRIPTION)

    var selectedAIModel by mutableStateOf("")
    var selectedAIModelDescription by mutableStateOf(defaultAIModelDescription)
    var availableAIModels by mutableStateOf(listOf<ModelInfo>())
    /** True when the last model-list request failed (the sidebar then shows only "All"). */
    var modelsUnavailable by mutableStateOf(false)
    val selectedAIModelOrDefault
        get() = selectedAIModel.ifEmpty { defaultAIModel }

    /** Loads the model list from the server; safe to call again as a refresh. Never throws. */
    fun refreshAIModels() {
        ChatViewModel.runBlocking {
            applyCatalog(modelsApi.fetch())
        }
    }

    private fun applyCatalog(result: Result<ModelCatalog>) {
        val catalog = result.getOrNull()
        if (catalog == null) {
            println("ERROR: Failed to load model list: ${result.exceptionOrNull()?.message}")
            modelsUnavailable = true
            return
        }
        modelsUnavailable = false
        availableAIModels = catalog.models
        val default = catalog.models.firstOrNull { it.id == catalog.defaultId }
        defaultAIModel = default?.id ?: FALLBACK_MODEL_ID
        defaultAIModelDescription = default?.name ?: FALLBACK_DESCRIPTION
        val selected = catalog.models.firstOrNull { it.id == selectedAIModel }
        if (selected == null) {
            selectedAIModel = ""
            selectedAIModelDescription = defaultAIModelDescription
        } else {
            selectedAIModelDescription = selected.name
        }
    }

    fun selectAIModel(model: ModelInfo) {
        if (selectedAIModel == model.id) return
        if (model in availableAIModels) {
            selectedAIModel = model.id
            selectedAIModelDescription = model.name
            ChatViewModel.runBlocking {
                initializeModel(webSocketClient, model.id)
            }
        }
    }
    fun deselectAIModel() {
        if (selectedAIModel.isEmpty()) return
        selectedAIModel = ""
        selectedAIModelDescription = defaultAIModelDescription
        ChatViewModel.runBlocking {
            initializeModel(webSocketClient)
        }
    }
    suspend fun initializeModel(
        client: ChatWebSocketClient,
        model: String = defaultAIModel,
        failureCallback: () -> Unit = {}
    ) {
        client.deleteSession()
        val result = client.createSession(model)
        if (!result.isSuccess) {
            println("ERROR: Failed to create WebSocket session: ${result.exceptionOrNull()?.message}")
            failureCallback()
        }
    }

    var chatRoomList by mutableStateOf(mapOf<Int, Pair<Boolean, String>>())
    var selectedChatRoom by mutableStateOf(-1)
}
