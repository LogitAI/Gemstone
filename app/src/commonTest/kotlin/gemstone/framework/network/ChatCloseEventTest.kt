package gemstone.framework.network

import gemstone.framework.network.websocket.ChatEvent
import gemstone.framework.network.websocket.closeEventFor
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue


class ChatCloseEventTest {
    @Test
    fun normalCloseCompletesTheReply() {
        assertEquals(ChatEvent.MessageComplete, closeEventFor(1000, ""))
        assertEquals(ChatEvent.MessageComplete, closeEventFor(1000, "bye"))
    }

    @Test
    fun aCloseWithoutStatusCompletesTheReply() {
        assertEquals(ChatEvent.MessageComplete, closeEventFor(null, null))
        assertEquals(ChatEvent.MessageComplete, closeEventFor(1005, null))
    }

    @Test
    fun offlineOrFailedDownloadSurfacesTheServersReason() {
        val reason = "Model 'Qwen/Qwen3-0.6B' is offline and not downloaded. Connect once to download it."
        assertEquals(ChatEvent.ErrorOccurred(reason), closeEventFor(1011, reason))
    }

    @Test
    fun busyAndUnknownModelAreErrorsToo() {
        assertEquals(ChatEvent.ErrorOccurred("server busy"), closeEventFor(1013, "server busy"))
        assertEquals(
            ChatEvent.ErrorOccurred("Model not found or failed to load."),
            closeEventFor(1008, " Model not found or failed to load. ")
        )
    }

    @Test
    fun anErrorCloseWithoutAReasonStillSaysSomething() {
        val event = closeEventFor(1006, "")
        assertIs<ChatEvent.ErrorOccurred>(event)
        assertTrue("1006" in event.error)
    }
}
