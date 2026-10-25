package gemstone.framework.storage

import android.util.AtomicFile
import gemstone.framework.network.settings.AndroidSettings
import java.io.File


/** `chats.json` in the app's private files directory, which no other app can read. */
class AppFilesChatFile(private val directory: File) : ChatFile {
    private val file = AtomicFile(File(directory, "chats.json"))

    override fun read(): String? = if (file.baseFile.exists()) file.readFully().decodeToString() else null

    override fun write(text: String) {
        val stream = file.startWrite()
        try {
            stream.write(text.encodeToByteArray())
            file.finishWrite(stream)
        } catch (e: Exception) {
            file.failWrite(stream)
            throw e
        }
    }

    override fun keepUnreadable() {
        file.baseFile.renameTo(File(directory, "chats.unreadable.json"))
    }
}


actual fun platformChatFile(): ChatFile = AppFilesChatFile(AndroidSettings.context.filesDir)
