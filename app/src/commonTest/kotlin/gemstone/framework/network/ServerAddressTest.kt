package gemstone.framework.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull


class ServerAddressTest {
    private fun parse(input: String) = ServerAddress.parse(input)

    @Test
    fun bareHostUsesTheDefaultPortAndPlainHttp() {
        assertEquals(ServerAddress(false, "127.0.0.1", 23100), parse("127.0.0.1"))
        assertEquals(ServerAddress(false, "my-box.local", 23100), parse("my-box.local"))
        assertEquals(ServerAddress(false, "10.0.0.2", 9000), ServerAddress.parse("10.0.0.2", defaultPort = 9000))
    }

    @Test
    fun hostAndPort() {
        assertEquals(ServerAddress(false, "192.168.0.5", 11434), parse("192.168.0.5:11434"))
        assertEquals(ServerAddress(false, "localhost", 23100), parse(" localhost:23100 "))
    }

    @Test
    fun bracketedIpv6() {
        assertEquals(ServerAddress(false, "::1", 23100), parse("[::1]:23100"))
        assertEquals(ServerAddress(false, "fe80::1", 23100), parse("[fe80::1]"))
        assertEquals(ServerAddress(false, "::1", 23100), parse("::1")) // bare IPv6 has no port to split
        assertEquals("[::1]:8080", parse("[::1]:8080").authority)
    }

    @Test
    fun fullUrlsKeepTheirSchemeAndFallBackToTheSchemeDefaultPort() {
        assertEquals(ServerAddress(false, "example.com", 80), parse("http://example.com"))
        assertEquals(ServerAddress(true, "example.com", 443), parse("https://example.com"))
        assertEquals(ServerAddress(true, "example.com", 8443), parse("HTTPS://example.com:8443/"))
        assertEquals(ServerAddress(false, "10.0.0.2", 23100), parse("http://10.0.0.2:23100/api/models?x=1"))
        assertEquals(ServerAddress(true, "::1", 8443), parse("https://[::1]:8443"))
        assertEquals(ServerAddress(true, "example.com", 443), parse("wss://example.com"))
    }

    @Test
    fun schemeSelectsHttpOrHttpsAndWsOrWss() {
        val plain = parse("http://h:1")
        assertEquals("http://h:1/api/models", plain.httpUrl("/api/models"))
        assertEquals("ws://h:1/api/chat/streaming", plain.wsUrl("/api/chat/streaming"))
        val tls = parse("https://h:2")
        assertEquals("https://h:2/api/models", tls.httpUrl("/api/models"))
        assertEquals("wss://h:2/api/chat/streaming", tls.wsUrl("/api/chat/streaming"))
    }

    @Test
    fun portWithOrWithoutALeadingColon() {
        assertEquals(ServerAddress(false, "127.0.0.1", 23100), ServerAddress.resolve(null, "23100"))
        assertEquals(ServerAddress(false, "127.0.0.1", 23100), ServerAddress.resolve(null, ":23100"))
        assertEquals(ServerAddress(false, "box", 5), ServerAddress.resolve("box", " :5 "))
        assertEquals(ServerAddress(true, "box", 8443), ServerAddress.resolve("https://box", "8443"))
    }

    @Test
    fun resolveDefaultsAndPrecedence() {
        assertEquals(ServerAddress(false, "127.0.0.1", 23100), ServerAddress.resolve(null, null))
        assertEquals(ServerAddress(false, "127.0.0.1", 23100), ServerAddress.resolve("", ""))
        assertEquals(ServerAddress(false, "box", 7), ServerAddress.resolve("box:7", null)) // port kept when none is given
        assertEquals(ServerAddress(false, "box", 8), ServerAddress.resolve("box:7", "8")) // an explicit port wins
    }

    @Test
    fun rejectsWhatIsNotAnAddress() {
        assertFailsWith<IllegalArgumentException> { parse("ftp://example.com") }
        assertFailsWith<IllegalArgumentException> { parse("host:notaport") }
        assertFailsWith<IllegalArgumentException> { parse("host:0") }
        assertFailsWith<IllegalArgumentException> { parse("host:70000") }
        assertFailsWith<IllegalArgumentException> { parse("[::1") }
        assertFailsWith<IllegalArgumentException> { parse("http://") }
    }

    @Test
    fun webOriginIsTheDefaultWhenNoPortIsGiven() {
        // window.location.origin omits the default port
        assertEquals(ServerAddress(false, "localhost", 80), parse("http://localhost"))
        assertEquals(ServerAddress(true, "app.example.com", 443), parse("https://app.example.com"))
        assertEquals(ServerAddress(false, "localhost", 23100), parse("http://localhost:23100"))
    }
}


class ApiKeyTest {
    @Test
    fun bearerHeaderOnlyWhenAKeyIsSet() {
        assertEquals("Bearer secret", ApiKey.authorization("secret"))
        assertEquals("Bearer secret", ApiKey.authorization("  secret\n"))
        assertNull(ApiKey.authorization(null))
        assertNull(ApiKey.authorization(""))
        assertNull(ApiKey.authorization("   "))
    }
}
