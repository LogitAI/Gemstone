package gemstone.framework.network.http

import gemstone.framework.network.LaunchSettings
import gemstone.framework.network.SecretStore
import gemstone.framework.network.ServerAddress
import gemstone.framework.network.ServerSettings
import gemstone.framework.network.SettingsStore
import io.ktor.client.*
import io.ktor.client.engine.cio.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.websocket.*
import io.ktor.serialization.kotlinx.json.*


/** A setting from the platform: JVM system property or environment variable; the environment on iOS. */
internal expect fun setting(name: String): String?


/** The platform's preferences, for the server address. */
internal expect fun platformSettingsStore(): SettingsStore


/** The platform's secure storage, for the API key (SPEC S2.7). */
internal expect fun platformSecretStore(): SecretStore


actual fun platformServerSettings(): ServerSettings {
    // GEMSTONE_SERVER_HOST / _PORT (or the desktop --server, --host, --port) and GEMSTONE_API_KEY win for this run
    val host = setting("GEMSTONE_SERVER_HOST")?.takeIf { it.isNotBlank() }
    val port = setting("GEMSTONE_SERVER_PORT")?.takeIf { it.isNotBlank() }
    val address = if (host != null || port != null) ServerAddress.resolve(host, port) else null
    val launch = LaunchSettings(address, setting("GEMSTONE_API_KEY")?.takeIf { it.isNotBlank() })
    return ServerSettings(launch, platformSettingsStore(), platformSecretStore())
}


actual object HttpClientFactory {
    actual fun create(): HttpClient {
        return HttpClient(CIO) {
            install(ContentNegotiation) {
                json()
            }
            install(WebSockets)
            engine {
                maxConnectionsCount = 1000
                endpoint {
                    maxConnectionsPerRoute = 100
                    pipelineMaxSize = 20
                    keepAliveTime = 5000
                    connectTimeout = 5000
                    requestTimeout = 15000
                }
            }
        }
    }
}
