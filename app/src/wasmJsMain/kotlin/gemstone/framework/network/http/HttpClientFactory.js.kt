package gemstone.framework.network.http

import gemstone.framework.network.LaunchSettings
import gemstone.framework.network.MemorySecretStore
import gemstone.framework.network.MemorySettingsStore
import gemstone.framework.network.ServerAddress
import gemstone.framework.network.ServerSettings
import io.ktor.client.*
import io.ktor.client.engine.js.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.websocket.*
import io.ktor.serialization.kotlinx.json.*
import kotlinx.browser.window


/**
 * The page's own protocol, host and port (80/443 when the origin omits them): the API serves the web
 * client (S1.9). Nothing is editable and no key is sent: a browser cannot set headers on a WebSocket.
 */
actual fun platformServerSettings(): ServerSettings = ServerSettings(
    LaunchSettings(address = ServerAddress.parse(window.location.origin)),
    MemorySettingsStore(),
    MemorySecretStore(),
    editable = false
)


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
