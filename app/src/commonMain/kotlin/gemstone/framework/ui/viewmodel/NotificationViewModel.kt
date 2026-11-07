package gemstone.framework.ui.viewmodel

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue


data class AppNotification(
    val id: Long,
    val title: String,
    val message: String,
    val read: Boolean = false
)


/** In-app notifications (SPEC S2.10, #206): finished long replies and model downloads. */
object NotificationViewModel {
    const val DEFAULT_LONG_REPLY_SECONDS = 10f
    private const val MAX_ITEMS = 50

    var longReplyThresholdSeconds: Float = DEFAULT_LONG_REPLY_SECONDS
    var dialogOpen by mutableStateOf(false)
        private set
    var items by mutableStateOf(emptyList<AppNotification>())
        private set

    val hasUnread: Boolean
        get() = items.any { !it.read }

    private var nextId = 1L

    fun open() {
        dialogOpen = true
        items = items.map { if (it.read) it else it.copy(read = true) }
    }

    fun close() {
        dialogOpen = false
    }

    fun clear() {
        items = emptyList()
    }

    /** Records a notification when a chat reply took at least [longReplyThresholdSeconds]. */
    fun notifyReplyCompleted(promptOrTitle: String, elapsedSeconds: Float) {
        if (elapsedSeconds < longReplyThresholdSeconds) return
        val label = promptOrTitle.lineSequence().firstOrNull()?.trim().orEmpty().take(40).ifEmpty { "Chat" }
        val seconds = elapsedSeconds.toInt().coerceAtLeast(1)
        add(
            title = "Reply finished",
            message = "\"$label\" completed (${seconds}s)"
        )
    }

    /** Records a notification when a model download (`POST /api/pull`) completes or fails. */
    fun notifyModelDownload(modelId: String, success: Boolean, detail: String? = null) {
        if (success) {
            add(
                title = "Model downloaded",
                message = modelId
            )
        } else {
            val suffix = detail?.takeIf { it.isNotBlank() }?.let { ": $it" }.orEmpty()
            add(
                title = "Model download failed",
                message = "$modelId$suffix"
            )
        }
    }

    private fun add(title: String, message: String) {
        val entry = AppNotification(
            id = nextId++,
            title = title,
            message = message,
            read = dialogOpen
        )
        items = (listOf(entry) + items).take(MAX_ITEMS)
    }
}
