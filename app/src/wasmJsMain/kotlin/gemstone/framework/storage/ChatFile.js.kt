package gemstone.framework.storage

import kotlinx.browser.localStorage


/** The browser's `localStorage` for the server's origin. */
class LocalStorageChatFile : ChatFile {
    override fun read(): String? = localStorage.getItem(KEY)

    override fun write(text: String) {
        localStorage.setItem(KEY, text)
    }

    override fun keepUnreadable() {
        localStorage.getItem(KEY)?.let { localStorage.setItem("$KEY.unreadable", it) }
        localStorage.removeItem(KEY)
    }

    companion object {
        const val KEY = "gemstone.chats"
    }
}


actual fun platformChatFile(): ChatFile = LocalStorageChatFile()
