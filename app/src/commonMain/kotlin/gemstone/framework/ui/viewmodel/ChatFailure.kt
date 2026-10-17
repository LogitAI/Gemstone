package gemstone.framework.ui.viewmodel


/*
 * A failed turn (SPEC S2.3, #39): the server cannot be reached, the prompt cannot be sent, the
 * server closes with an error, or the reply is empty. Pure, so it is tested without a socket.
 */

/**
 * The state after the turn in flight failed with [error]. The turn's bubble and partial reply go,
 * and its prompt is kept for Retry. A second report of the same failure (a close error arrives as
 * a state and as an event) keeps the prompt of the first.
 */
fun ChatUiState.failTurn(error: String): ChatUiState = copy(
    currentMessage = null,
    failedPrompt = currentMessage?.user ?: failedPrompt,
    error = error,
    isResponding = false
)

/** The state after the user dismissed the error: the failed prompt goes back in an empty input. */
fun ChatUiState.dismissError(): ChatUiState = copy(
    error = null,
    failedPrompt = null,
    messageInput = if (messageInput.isEmpty()) failedPrompt.orEmpty() else messageInput
)

/** The state to send again on Retry, or null when there is no failed prompt. */
fun ChatUiState.retryInput(): ChatUiState? = failedPrompt?.let {
    copy(error = null, failedPrompt = null, messageInput = it)
}

/** Drops every record from [index] on: the records the failed turn added. */
fun ChatHistory.dropFrom(index: Int) {
    while (size > index) removeAt(lastIndex)
}
