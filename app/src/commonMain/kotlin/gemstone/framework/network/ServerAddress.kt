package gemstone.framework.network

import io.ktor.client.request.*
import io.ktor.http.*


/**
 * Where the model server is (SPEC S2.2): scheme, host and port, parsed from `host`, `host:port`,
 * `[ipv6]:port` or a full `http(s)://host[:port]` URL. `https` goes with `wss`, `http` with `ws`.
 */
data class ServerAddress(val secure: Boolean, val host: String, val port: Int) {
    /** `host:port`, with an IPv6 host in brackets. */
    val authority: String get() = (if (':' in host) "[$host]" else host) + ":$port"

    fun httpUrl(path: String): String = "${if (secure) "https" else "http"}://$authority$path"

    fun wsUrl(path: String): String = "${if (secure) "wss" else "ws"}://$authority$path"

    override fun toString(): String = httpUrl("")

    companion object {
        const val DEFAULT_HOST = "127.0.0.1"
        const val DEFAULT_PORT = 23100

        /**
         * [input] is `host`, `host:port`, `[ipv6]:port` or `http(s)://host[:port]` (a path, query or
         * fragment is ignored). A port that is missing falls back to the scheme's (80/443) when a
         * scheme is given, else to [defaultPort]. Throws [IllegalArgumentException] if malformed.
         */
        fun parse(input: String, defaultPort: Int = DEFAULT_PORT): ServerAddress {
            var rest = input.trim()
            var secure = false
            var schemeGiven = false
            val schemeEnd = rest.indexOf("://")
            if (schemeEnd >= 0) {
                secure = when (rest.substring(0, schemeEnd).lowercase()) {
                    "http", "ws" -> false
                    "https", "wss" -> true
                    else -> throw IllegalArgumentException("Unsupported scheme in server address: $input")
                }
                schemeGiven = true
                rest = rest.substring(schemeEnd + 3)
            }
            rest = rest.substringBefore('/').substringBefore('?').substringBefore('#')
            require(rest.isNotEmpty()) { "Empty host in server address: $input" }

            val host: String
            val portText: String?
            if (rest.startsWith("[")) {
                val close = rest.indexOf(']')
                require(close > 1) { "Unterminated IPv6 address: $input" }
                host = rest.substring(1, close)
                val tail = rest.substring(close + 1)
                require(tail.isEmpty() || tail.startsWith(":")) { "Unexpected text after IPv6 address: $input" }
                portText = tail.removePrefix(":").ifEmpty { null }
            } else if (rest.count { it == ':' } == 1) {
                host = rest.substringBefore(':')
                portText = rest.substringAfter(':').ifEmpty { null }
            } else {
                host = rest // no colon, or an unbracketed IPv6 address (no port can be told apart)
                portText = null
            }
            require(host.isNotEmpty()) { "Empty host in server address: $input" }

            val port = if (portText != null) parsePort(portText)
            else if (schemeGiven) (if (secure) 443 else 80)
            else defaultPort
            return ServerAddress(secure, host, port)
        }

        /**
         * The address from the two settings `GEMSTONE_SERVER_HOST` ([host]: anything [parse] takes) and
         * `GEMSTONE_SERVER_PORT` ([port]: `23100` or `:23100`). An explicit [port] wins over one in [host].
         */
        fun resolve(host: String?, port: String?): ServerAddress {
            val base = parse(host?.takeIf { it.isNotBlank() } ?: DEFAULT_HOST)
            val portText = port?.trim()?.removePrefix(":")?.trim()?.takeIf { it.isNotEmpty() } ?: return base
            return base.copy(port = parsePort(portText))
        }

        private fun parsePort(text: String): Int {
            val port = text.trim().toIntOrNull()
            require(port != null && port in 1..65535) { "Invalid port: $text" }
            return port
        }
    }
}


/** `GEMSTONE_API_KEY`: sent as `Authorization: Bearer <key>` on HTTP and WebSocket requests when set. */
object ApiKey {
    /** The header value for [key], or null when there is no key (unset or blank). */
    fun authorization(key: String?): String? = key?.trim()?.takeIf { it.isNotEmpty() }?.let { "Bearer $it" }
}


/** Adds the bearer header when [key] is set; a no-op otherwise. */
fun HttpRequestBuilder.authorize(key: String?) {
    ApiKey.authorization(key)?.let { header(HttpHeaders.Authorization, it) }
}
