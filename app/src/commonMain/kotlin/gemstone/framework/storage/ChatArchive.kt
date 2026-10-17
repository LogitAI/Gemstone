package gemstone.framework.storage

import gemstone.framework.ui.viewmodel.ChatMessage
import gemstone.framework.ui.viewmodel.Conversation
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.random.Random
import kotlin.time.Clock


/** The platform's `chats.json` (SPEC S2.6). */
interface ChatFile {
    /** The file's text, or null when there is no file. */
    fun read(): String?

    /** Replaces the file's text in one step, so a crash leaves the old or the new file. */
    fun write(text: String)

    /** Renames the file to `chats.unreadable.json`, so a file that cannot be read is not lost. */
    fun keepUnreadable()
}


@Serializable
data class SavedChat(
    val id: String,
    val title: String,
    val createdAt: Long,
    val updatedAt: Long,
    val turns: List<Conversation>,
    val history: List<ChatMessage>
)


@Serializable
data class ChatArchiveFile(val version: Int, val chats: List<SavedChat>)


/** The saved chats (SPEC S2.6). */
class ChatArchive(
    private val file: ChatFile,
    private val now: () -> Long = { Clock.System.now().toEpochMilliseconds() }
) {
    /** Most recently updated first. */
    var chats: List<SavedChat> = emptyList()
        private set

    /** False when the file has a newer version: it is left untouched. */
    var writable: Boolean = true
        private set

    fun load() {
        chats = emptyList()
        writable = true
        val text = file.read() ?: return
        val parsed = try {
            val version = json.parseToJsonElement(text).jsonObject["version"]?.jsonPrimitive?.intOrNull
            if (version != null && version > VERSION) {
                // A newer app wrote it: neither read nor overwrite what this version does not understand
                println("WARNING: chats.json has version $version, newer than $VERSION; chats are not saved this run")
                writable = false
                return
            }
            json.decodeFromString<ChatArchiveFile>(text)
        } catch (e: Exception) {
            println("WARNING: chats.json cannot be read (${e.message}); kept as chats.unreadable.json")
            file.keepUnreadable()
            return
        }
        chats = parsed.chats.sortedByDescending { it.updatedAt }
    }

    /** Saves the chat [id] (a new one when null); returns it, or null when saving is disabled. */
    fun save(id: String?, turns: List<Conversation>, history: List<ChatMessage>): SavedChat? {
        if (!writable) return null
        val existing = chats.firstOrNull { it.id == id }
        val time = now()
        val chat = SavedChat(
            id = existing?.id ?: id ?: newId(time),
            title = existing?.title ?: titleFor(turns.firstOrNull()?.user.orEmpty()),
            createdAt = existing?.createdAt ?: time,
            updatedAt = time,
            turns = turns,
            history = history
        )
        store(listOf(chat) + chats.filterNot { it.id == chat.id })
        return chat
    }

    fun delete(id: String) {
        if (writable) store(chats.filterNot { it.id == id })
    }

    fun deleteAll() {
        if (writable) store(emptyList())
    }

    private fun store(list: List<SavedChat>) {
        chats = list.sortedByDescending { it.updatedAt }
        file.write(json.encodeToString(ChatArchiveFile.serializer(), ChatArchiveFile(VERSION, chats)))
    }

    private fun newId(time: Long): String = "$time-${Random.nextInt(0, Int.MAX_VALUE).toString(16)}"

    companion object {
        const val VERSION = 1
        const val TITLE_LENGTH = 40

        /** The first line of the first prompt, cut to [TITLE_LENGTH] characters. */
        fun titleFor(prompt: String): String = prompt.trim().lineSequence().first().trim().take(TITLE_LENGTH)

        // Fields added by a later version are ignored (SPEC S2.6)
        private val json = Json { ignoreUnknownKeys = true }
    }
}
