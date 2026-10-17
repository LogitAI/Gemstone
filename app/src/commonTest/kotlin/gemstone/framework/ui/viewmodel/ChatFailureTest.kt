package gemstone.framework.ui.viewmodel

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull


class ChatFailureTest {
    private val done = Conversation(user = "Hi", assistant = "Hello!")
    private val inFlight = ChatUiState(
        messages = listOf(done),
        currentMessage = Conversation(user = "What is the weather?", assistant = "It is"),
        isResponding = true
    )

    @Test
    fun aFailedTurnShowsTheErrorAndRemovesTheTurnFromTheScreen() {
        val failed = inFlight.failTurn("Model is busy.")
        assertEquals("Model is busy.", failed.error)
        assertEquals("What is the weather?", failed.failedPrompt)
        assertNull(failed.currentMessage)  // the bubble and its partial reply are gone
        assertEquals(listOf(done), failed.messages)
        assertFalse(failed.isResponding)
    }

    @Test
    fun aSecondReportOfTheSameFailureKeepsThePrompt() {
        // A close error arrives both as a state and as an event.
        val failed = inFlight.failTurn("Model is busy.").failTurn("Model is busy.")
        assertEquals("What is the weather?", failed.failedPrompt)
    }

    @Test
    fun retrySendsTheFailedPromptAgain() {
        val retry = inFlight.failTurn("Connection refused").retryInput()
        assertEquals("What is the weather?", retry?.messageInput)
        assertNull(retry?.error)
        assertNull(retry?.failedPrompt)
    }

    @Test
    fun retryWithoutAFailedPromptDoesNothing() {
        assertNull(ChatUiState(error = "Disconnect failed").retryInput())
    }

    @Test
    fun dismissPutsThePromptBackInTheInput() {
        val dismissed = inFlight.failTurn("Connection refused").dismissError()
        assertNull(dismissed.error)
        assertNull(dismissed.failedPrompt)
        assertEquals("What is the weather?", dismissed.messageInput)
    }

    @Test
    fun dismissKeepsWhatWasTypedSince() {
        val dismissed = inFlight.failTurn("Connection refused").copy(messageInput = "new text").dismissError()
        assertEquals("new text", dismissed.messageInput)
    }

    @Test
    fun theFailedTurnsHistoryRecordsAreDropped() {
        val history = ChatHistory()
        history.add(ChatRole.USER.value, "Hi")
        history.add(ChatRole.ASSISTANT.value, "Hello!")
        val turnStart = history.size
        history.add(ChatRole.USER.value, "What is the weather?")
        history.add(ChatRole.TOOL.value, "{\"temp\": 20}", tool_call_id = "call_1")

        history.dropFrom(turnStart)
        assertEquals(listOf("Hi", "Hello!"), history.map { it.content })

        history.dropFrom(turnStart)  // a second report drops nothing more
        assertEquals(2, history.size)
    }
}
