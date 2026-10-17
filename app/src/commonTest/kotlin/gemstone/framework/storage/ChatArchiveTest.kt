package gemstone.framework.storage

import gemstone.framework.ui.viewmodel.ChatMessage
import gemstone.framework.ui.viewmodel.Conversation
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue


class MemoryChatFile(var text: String? = null) : ChatFile {
    var writes = 0
    var keptUnreadable: String? = null
    override fun read() = text
    override fun write(text: String) {
        this.text = text
        writes++
    }
    override fun keepUnreadable() {
        keptUnreadable = text
        text = null
    }
}


class ChatArchiveTest {
    private var clock = 1_000L
    private val file = MemoryChatFile()
    private fun archive() = ChatArchive(file) { clock }.also { it.load() }

    private val turns = listOf(Conversation(user = "What is Gemstone?\nAnswer briefly.", assistant = "A local AI chat."))
    private val history = listOf(
        ChatMessage("user", "What is Gemstone?\nAnswer briefly."),
        ChatMessage("assistant", "A local AI chat.")
    )

    @Test
    fun aMissingFileStartsEmpty() {
        val a = archive()
        assertTrue(a.chats.isEmpty())
        assertTrue(a.writable)
    }

    @Test
    fun aSavedChatComesBackAfterARestart() {
        val saved = archive().save(null, turns, history)
        assertNotNull(saved)
        val restarted = archive()
        assertEquals(listOf(saved), restarted.chats)
        assertEquals(turns, restarted.chats[0].turns)
        assertEquals(history, restarted.chats[0].history)
        assertEquals("What is Gemstone?", restarted.chats[0].title)
    }

    @Test
    fun theFileCarriesItsVersion() {
        archive().save(null, turns, history)
        val version = Json.parseToJsonElement(file.text!!).jsonObject["version"]!!.jsonPrimitive.int
        assertEquals(ChatArchive.VERSION, version)
    }

    @Test
    fun savingAgainUpdatesTheSameChat() {
        val a = archive()
        val first = a.save(null, turns, history)!!
        clock = 2_000L
        val longer = turns + Conversation(user = "And offline?", assistant = "Planned.")
        val second = a.save(first.id, longer, history)!!
        assertEquals(1, a.chats.size)
        assertEquals(first.id, second.id)
        assertEquals(first.title, second.title)
        assertEquals(1_000L, second.createdAt)
        assertEquals(2_000L, second.updatedAt)
        assertEquals(longer, archive().chats.single().turns)
    }

    @Test
    fun chatsAreListedMostRecentFirst() {
        val a = archive()
        val older = a.save(null, turns, history)!!
        clock = 2_000L
        val newer = a.save(null, listOf(Conversation(user = "Second chat")), emptyList())!!
        assertEquals(listOf(newer.id, older.id), a.chats.map { it.id })
        clock = 3_000L
        a.save(older.id, turns, history)
        assertEquals(listOf(older.id, newer.id), archive().chats.map { it.id })
    }

    @Test
    fun theTitleIsTheFirstLineCutToFortyCharacters() {
        assertEquals("Hello", ChatArchive.titleFor("  Hello  \nworld"))
        assertEquals("a".repeat(40), ChatArchive.titleFor("a".repeat(100)))
    }

    @Test
    fun deletingRemovesOneChatFromTheFile() {
        val a = archive()
        val keep = a.save(null, turns, history)!!
        val gone = a.save(null, listOf(Conversation(user = "Delete me")), emptyList())!!
        a.delete(gone.id)
        assertEquals(listOf(keep.id), a.chats.map { it.id })
        assertEquals(listOf(keep.id), archive().chats.map { it.id })
        assertFalse("Delete me" in file.text!!)
    }

    @Test
    fun deleteAllEmptiesTheFile() {
        val a = archive()
        a.save(null, turns, history)
        a.deleteAll()
        assertTrue(a.chats.isEmpty())
        assertTrue(archive().chats.isEmpty())
        assertFalse("Gemstone" in file.text!!)
    }

    @Test
    fun aNewerVersionIsLeftUntouched() {
        val newer = """{"version": 2, "chats": [{"id": "x", "format": "unknown to version 1"}]}"""
        file.text = newer
        val a = archive()
        assertFalse(a.writable)
        assertTrue(a.chats.isEmpty())
        assertNull(a.save(null, turns, history))
        a.deleteAll()
        assertEquals(newer, file.text)
        assertEquals(0, file.writes)
    }

    @Test
    fun anUnreadableFileIsKeptAside() {
        file.text = "{not json"
        val a = archive()
        assertEquals("{not json", file.keptUnreadable)
        assertTrue(a.chats.isEmpty())
        assertTrue(a.writable)
        assertNotNull(a.save(null, turns, history))
    }

    @Test
    fun unknownFieldsAreIgnored() {
        archive().save(null, turns, history)
        file.text = file.text!!.replaceFirst("{", """{"addedLater": true, """)
        assertEquals(1, archive().chats.size)
    }
}
