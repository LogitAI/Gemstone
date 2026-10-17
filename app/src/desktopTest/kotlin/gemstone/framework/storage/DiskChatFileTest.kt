package gemstone.framework.storage

import java.io.File
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue


/** The desktop `chats.json` (SPEC S2.6), in a directory under the module's build directory. */
class DiskChatFileTest {
    private val directory = File("build/tmp/disk-chat-file-test/${System.nanoTime()}")
    private val file = DiskChatFile(directory)

    @AfterTest
    fun cleanUp() {
        directory.deleteRecursively()
    }

    @Test
    fun aMissingFileReadsAsNothing() {
        assertNull(file.read())
    }

    @Test
    fun writingCreatesTheDirectoryAndReplacesTheFile() {
        file.write("""{"version": 1, "chats": []}""")
        file.write("second")
        assertEquals("second", file.read())
        assertEquals(listOf("chats.json"), directory.list()!!.sorted())  // no temporary file left behind
    }

    @Test
    fun onlyTheOwnerCanReadTheFile() {
        if ("windows" in System.getProperty("os.name").lowercase()) return  // NTFS keeps the profile's ACL
        file.write("private")
        val permissions = Files.getPosixFilePermissions(File(directory, "chats.json").toPath())
        assertEquals(PosixFilePermissions.fromString("rw-------"), permissions)
        assertEquals(PosixFilePermissions.fromString("rwx------"), Files.getPosixFilePermissions(directory.toPath()))
    }

    @Test
    fun anUnreadableFileIsKeptAside() {
        file.write("{not json")
        file.keepUnreadable()
        assertNull(file.read())
        assertTrue(File(directory, "chats.unreadable.json").readText() == "{not json")
        assertFalse(File(directory, "chats.json").exists())
    }

    @Test
    fun theArchiveRoundTripsThroughTheDisk() {
        ChatArchive(file).apply { load() }.save(null, listOf(gemstone.framework.ui.viewmodel.Conversation(user = "Hi", assistant = "Hello")), emptyList())
        val reloaded = ChatArchive(file).apply { load() }
        assertEquals("Hi", reloaded.chats.single().title)
    }
}
