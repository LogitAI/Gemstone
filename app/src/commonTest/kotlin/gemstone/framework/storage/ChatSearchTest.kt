package gemstone.framework.storage

import gemstone.framework.ui.viewmodel.Conversation
import kotlin.test.Test
import kotlin.test.assertEquals


class ChatSearchTest {
    private fun chat(id: String, title: String, vararg turns: Pair<String, String>) =
        SavedChat(id, title, 0, 0, turns.map { Conversation(user = it.first, assistant = it.second) }, emptyList())

    private val weather = chat("1", "Weather in Seoul", "Weather in Seoul?" to "It is sunny.")
    private val uv = chat("2", "Install Gemstone", "How do I install it?" to "Run uv sync, then uv run.")
    private val code = chat("3", "Kotlin question", "What does SelectionContainer do?" to "It makes text selectable.")
    private val chats = listOf(weather, uv, code)

    @Test
    fun aBlankQueryListsEveryChat() {
        assertEquals(chats, searchChats(chats, ""))
        assertEquals(chats, searchChats(chats, "   "))
    }

    @Test
    fun theTitleMatchesIgnoringCase() {
        assertEquals(listOf(weather), searchChats(chats, "seoul"))
    }

    @Test
    fun aPromptOrAReplyMatches() {
        assertEquals(listOf(code), searchChats(chats, "selectioncontainer"))
        assertEquals(listOf(uv), searchChats(chats, "UV SYNC"))
    }

    @Test
    fun theOrderIsKept() {
        // Every chat contains "it"; the result keeps the given order, whatever it is
        assertEquals(listOf(code, uv, weather), searchChats(listOf(code, uv, weather), "it"))
    }

    @Test
    fun noMatchListsNothing() {
        assertEquals(emptyList(), searchChats(chats, "torchnative"))
    }

    @Test
    fun theQueryIsTrimmed() {
        assertEquals(listOf(weather), searchChats(chats, "  sunny "))
    }
}
