package gemstone.framework.network.http

import gemstone.framework.network.ServerAddress
import io.ktor.client.*
import io.ktor.client.engine.js.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.websocket.*
import io.ktor.serialization.kotlinx.json.*
import kotlinx.browser.window


/** The page's own protocol, host and port (80/443 when the origin omits them): the API serves the web client (S1.9). */
actual val defaultServerAddress: ServerAddress = ServerAddress.parse(window.location.origin)


/** A browser has no environment to read a key from. */
actual val defaultApiKey: String? = null


actual object HttpClientFactory {
    actual fun create(): HttpClient {
        return HttpClient(Js) {
            install(ContentNegotiation) {
                json()
            }
            install(WebSockets)
        }
    }
}
