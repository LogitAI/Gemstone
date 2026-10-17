package gemstone.framework.storage

import kotlinx.cinterop.BetaInteropApi
import kotlinx.cinterop.ExperimentalForeignApi
import platform.Foundation.NSApplicationSupportDirectory
import platform.Foundation.NSFileManager
import platform.Foundation.NSString
import platform.Foundation.NSURL
import platform.Foundation.NSUTF8StringEncoding
import platform.Foundation.NSUserDomainMask
import platform.Foundation.create
import platform.Foundation.writeToFile


/** `chats.json` in the app's Application Support directory, inside its sandbox. */
@OptIn(ExperimentalForeignApi::class, BetaInteropApi::class)
class AppSupportChatFile : ChatFile {
    private val manager = NSFileManager.defaultManager
    private val directory: String = (manager.URLsForDirectory(NSApplicationSupportDirectory, NSUserDomainMask).first() as NSURL).path!!
    private val path = "$directory/chats.json"

    override fun read(): String? =
        if (manager.fileExistsAtPath(path)) NSString.create(contentsOfFile = path, encoding = NSUTF8StringEncoding, error = null)?.toString() else null

    override fun write(text: String) {
        manager.createDirectoryAtPath(directory, withIntermediateDirectories = true, attributes = null, error = null)
        @Suppress("CAST_NEVER_SUCCEEDS")
        val written = (text as NSString).writeToFile(path, atomically = true, encoding = NSUTF8StringEncoding, error = null)
        check(written) { "Could not write $path" }
    }

    override fun keepUnreadable() {
        val aside = "$directory/chats.unreadable.json"
        manager.removeItemAtPath(aside, error = null)
        manager.moveItemAtPath(path, toPath = aside, error = null)
    }
}


actual fun platformChatFile(): ChatFile = AppSupportChatFile()
