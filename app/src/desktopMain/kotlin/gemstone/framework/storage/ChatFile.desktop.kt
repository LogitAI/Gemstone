package gemstone.framework.storage

import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.PosixFilePermissions


/** The user's app-data directory: Application Support on macOS, %APPDATA% on Windows, XDG on Linux. */
private fun appDataDirectory(): File {
    val home = System.getProperty("user.home")
    val os = System.getProperty("os.name").orEmpty().lowercase()
    return when {
        "mac" in os -> File(home, "Library/Application Support/Gemstone")
        "windows" in os -> File(System.getenv("APPDATA")?.takeIf { it.isNotBlank() } ?: "$home/AppData/Roaming", "Gemstone")
        else -> File(System.getenv("XDG_DATA_HOME")?.takeIf { it.isNotBlank() } ?: "$home/.local/share", "gemstone")
    }
}


/** Owner-only access where the file system has POSIX permissions (macOS, Linux); Windows keeps the profile's ACL. */
private fun ownerOnly(file: File, permissions: String) {
    try {
        Files.setPosixFilePermissions(file.toPath(), PosixFilePermissions.fromString(permissions))
    } catch (_: UnsupportedOperationException) {
    }
}


class DiskChatFile(private val directory: File = appDataDirectory()) : ChatFile {
    private val file = File(directory, "chats.json")

    override fun read(): String? = if (file.exists()) file.readText() else null

    override fun write(text: String) {
        if (!directory.exists()) {
            directory.mkdirs()
            ownerOnly(directory, "rwx------")
        }
        val temporary = File(directory, "chats.json.tmp")
        temporary.writeText(text)
        ownerOnly(temporary, "rw-------")
        Files.move(temporary.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }

    override fun keepUnreadable() {
        Files.move(file.toPath(), File(directory, "chats.unreadable.json").toPath(), StandardCopyOption.REPLACE_EXISTING)
    }
}


actual fun platformChatFile(): ChatFile = DiskChatFile()
