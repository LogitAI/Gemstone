package gemstone.framework.ui.viewmodel

import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue


class NotificationViewModelTest {
    @BeforeTest
    fun resetNotifications() {
        NotificationViewModel.clear()
        NotificationViewModel.close()
        NotificationViewModel.longReplyThresholdSeconds = NotificationViewModel.DEFAULT_LONG_REPLY_SECONDS
    }

    @Test
    fun shortRepliesDoNotNotifyAndLongRepliesDo() {
        NotificationViewModel.notifyReplyCompleted("Quick question", elapsedSeconds = 3f)
        assertTrue(NotificationViewModel.items.isEmpty())
        assertFalse(NotificationViewModel.hasUnread)

        NotificationViewModel.notifyReplyCompleted("Deep analysis", elapsedSeconds = 12.5f)
        assertEquals(1, NotificationViewModel.items.size)
        assertTrue(NotificationViewModel.hasUnread)
        assertEquals("Reply finished", NotificationViewModel.items.first().title)
        assertTrue(NotificationViewModel.items.first().message.contains("Deep analysis"))
    }

    @Test
    fun modelDownloadSuccessAndFailureAreRecordedNewestFirst() {
        NotificationViewModel.notifyModelDownload("Qwen/Qwen3-0.6B", success = true)
        NotificationViewModel.notifyModelDownload("Qwen/BadModel", success = false, detail = "HTTP 404")

        assertEquals(2, NotificationViewModel.items.size)
        assertEquals("Model download failed", NotificationViewModel.items[0].title)
        assertTrue(NotificationViewModel.items[0].message.contains("Qwen/BadModel"))
        assertTrue(NotificationViewModel.items[0].message.contains("HTTP 404"))
        assertEquals("Model downloaded", NotificationViewModel.items[1].title)
        assertTrue(NotificationViewModel.items[1].message.contains("Qwen/Qwen3-0.6B"))
    }

    @Test
    fun openingMarksAllReadAndClearEmptiesTheList() {
        NotificationViewModel.notifyModelDownload("Qwen/Qwen3-0.6B", success = true)
        assertTrue(NotificationViewModel.hasUnread)

        NotificationViewModel.open()
        assertTrue(NotificationViewModel.dialogOpen)
        assertFalse(NotificationViewModel.hasUnread)
        assertTrue(NotificationViewModel.items.all { it.read })

        NotificationViewModel.clear()
        assertTrue(NotificationViewModel.items.isEmpty())
    }
}
