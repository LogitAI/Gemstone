package gemstone.framework.ui.viewmodel

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import gemstone.framework.network.ModelCatalog
import gemstone.framework.network.ModelInfo
import gemstone.framework.network.http.ModelsApi
import gemstone.framework.network.ChatTransport
import gemstone.framework.network.ChatTransports
import gemstone.framework.network.InAppChatTransport
import gemstone.framework.network.websocket.ChatWebSocketClient


/** The server transport (S1.4); [ChatTransports.active] is the one in use (S3.1). */
val webSocketClient: ChatWebSocketClient get() = ChatTransports.server


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

    /** Loads the model list from the active transport; safe to call again as a refresh. Never throws. */
    fun refreshAIModels() {
        ChatViewModel.runBlocking {
            applyCatalog(fetchCatalog())
        }
    }

    /** The server's list, or the models stored on the device for the in-app transport (S3.2: no request). */
    private suspend fun fetchCatalog(): Result<ModelCatalog> = when (val transport = ChatTransports.active) {
        is InAppChatTransport -> runCatching {
            val models = transport.models()
            ModelCatalog(models, models.firstOrNull()?.id)
        }
        else -> modelsApi.fetch()
    }

    /** Pulls [modelName] on the server (`POST /api/pull`), notifies on completion or failure, and refreshes the list. */
    fun pullModel(modelName: String) {
        val trimmed = modelName.trim()
        if (trimmed.isEmpty()) return
        if (ChatTransports.active is InAppChatTransport) {
            NotificationViewModel.notifyModelDownload(trimmed, success = false, detail = "Pulling a model onto the device is not available yet (#42)")
            return
        }
        ChatViewModel.runBlocking {
            val result = modelsApi.pull(trimmed)
            if (result.isSuccess) {
                NotificationViewModel.notifyModelDownload(trimmed, success = true)
                applyCatalog(fetchCatalog())
            } else {
                NotificationViewModel.notifyModelDownload(
                    trimmed,
                    success = false,
                    detail = result.exceptionOrNull()?.message
                )
            }
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
                initializeModel(ChatTransports.active, model.id)
            }
        }
    }
    fun deselectAIModel() {
        if (selectedAIModel.isEmpty()) return
        selectedAIModel = ""
        selectedAIModelDescription = defaultAIModelDescription
        ChatViewModel.runBlocking {
            initializeModel(ChatTransports.active)
        }
    }
    suspend fun initializeModel(
        client: ChatTransport,
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
}
