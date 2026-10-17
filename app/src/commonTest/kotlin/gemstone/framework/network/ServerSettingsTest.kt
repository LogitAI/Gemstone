package gemstone.framework.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue


class MemoryStore : SettingsStore {
    val values = mutableMapOf<String, String>()
    override fun get(key: String) = values[key]
    override fun set(key: String, value: String?) {
        if (value == null) values.remove(key) else values[key] = value
    }
}


class MemorySecrets(override val persistent: Boolean = true) : SecretStore {
    var value: String? = null
    override fun get() = value
    override fun set(value: String?) { this.value = value }
}


class ServerSettingsTest {
    private val store = MemoryStore()
    private val secrets = MemorySecrets()
    private fun settings(launch: LaunchSettings = LaunchSettings(), editable: Boolean = true) =
        ServerSettings(launch, store, secrets, editable)

    @Test
    fun withNothingSavedTheDefaultApplies() {
        val s = settings()
        assertEquals(ServerAddress(false, "127.0.0.1", 23100), s.address)
        assertNull(s.apiKey)
    }

    @Test
    fun aSavedAddressAndKeyApplyAtOnceAndAfterARestart() {
        assertNull(settings().save("192.168.0.5:8080", "s3cret"))
        val restarted = settings()
        for (s in listOf(settings(), restarted)) {
            assertEquals(ServerAddress(false, "192.168.0.5", 8080), s.address)
            assertEquals("s3cret", s.apiKey)
            assertEquals("192.168.0.5:8080", s.addressText)
        }
    }

    @Test
    fun theSavedChangeIsSeenByTheSameInstance() {
        val s = settings()
        s.save("https://gem.example", "")
        assertEquals(ServerAddress(true, "gem.example", 443), s.address)
    }

    @Test
    fun theKeyIsNeverWrittenToThePlainPreferences() {
        settings().save("192.168.0.5", "s3cret-key")
        assertEquals("s3cret-key", secrets.value)
        assertTrue(store.values.values.none { "s3cret-key" in it }, store.values.toString())
    }

    @Test
    fun aMalformedAddressSavesNothing() {
        val s = settings()
        s.save("10.0.0.1:9000", "old")
        val error = s.save("10.0.0.1:notaport", "new")
        assertNotNull(error)
        assertEquals(ServerAddress(false, "10.0.0.1", 9000), s.address)
        assertEquals("old", s.apiKey)
    }

    @Test
    fun emptyFieldsMeanTheDefaultAndNoKey() {
        val s = settings()
        s.save("10.0.0.1:9000", "k")
        assertNull(s.save("  ", ""))
        assertEquals(ServerAddress(false, "127.0.0.1", 23100), s.address)
        assertNull(s.apiKey)
        assertTrue(store.values.isEmpty())
        assertNull(secrets.value)
    }

    @Test
    fun aValueSetAtLaunchWinsAndIsNotOverwritten() {
        val launch = LaunchSettings(ServerAddress(false, "lan-host", 23100), "from-env")
        val s = settings(launch)
        assertTrue(s.addressSetAtLaunch)
        assertTrue(s.keySetAtLaunch)
        assertNull(s.save("10.0.0.1", "typed"))
        assertEquals(ServerAddress(false, "lan-host", 23100), s.address)
        assertEquals("from-env", s.apiKey)
        assertTrue(store.values.isEmpty())
        assertNull(secrets.value)
    }

    @Test
    fun onlyTheAddressCanBeSetAtLaunch() {
        val s = settings(LaunchSettings(address = ServerAddress(false, "lan-host", 23100)))
        assertFalse(s.keySetAtLaunch)
        s.save("ignored", "typed")
        assertEquals("typed", s.apiKey)
    }

    @Test
    fun aFailingSecureStorageSavesNothing() {
        val failing = object : SecretStore {
            override val persistent = true
            override fun get(): String? = null
            override fun set(value: String?) = throw IllegalStateException("Keychain error -25308")
        }
        val s = ServerSettings(LaunchSettings(), store, failing)
        val error = s.save("10.0.0.1", "typed")
        assertNotNull(error)
        assertTrue("-25308" in error, error)
        assertTrue(store.values.isEmpty())
        assertNull(s.apiKey)
    }

    @Test
    fun aKeyKeptForThisRunOnlyIsReported() {
        val s = ServerSettings(LaunchSettings(), store, MemorySecrets(persistent = false))
        assertFalse(s.keyPersistent)
    }

    @Test
    fun theWebSavesNothing() {
        val page = ServerAddress(true, "gem.example", 443)
        val s = settings(LaunchSettings(address = page), editable = false)
        s.save("10.0.0.1", "typed")
        assertEquals(page, s.address)
        assertNull(s.apiKey)
        assertTrue(store.values.isEmpty())
        assertNull(secrets.value)
    }
}
