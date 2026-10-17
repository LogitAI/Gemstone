package gemstone.framework.storage


/** `chats.json` where the platform keeps the user's app data (SPEC S2.6). */
expect fun platformChatFile(): ChatFile


/** The saved chats, loaded on first use. */
val chatArchive: ChatArchive by lazy { ChatArchive(platformChatFile()).also { it.load() } }
