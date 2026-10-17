package gemstone.framework.storage


/**
 * The saved chats whose title, prompts or replies contain [query], ignoring case, in the given
 * order (SPEC S2.6). A blank query matches every chat.
 */
fun searchChats(chats: List<SavedChat>, query: String): List<SavedChat> {
    val needle = query.trim()
    if (needle.isEmpty()) return chats
    return chats.filter { chat ->
        chat.title.contains(needle, ignoreCase = true) || chat.turns.any {
            it.user.contains(needle, ignoreCase = true) || it.assistant.contains(needle, ignoreCase = true)
        }
    }
}
