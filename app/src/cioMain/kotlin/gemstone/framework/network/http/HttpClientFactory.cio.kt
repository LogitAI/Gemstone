package gemstone.framework.network.http

import gemstone.framework.network.ServerAddress
import io.ktor.client.*
import io.ktor.client.engine.cio.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.websocket.*
import io.ktor.serialization.kotlinx.json.*


/** A setting from the platform: JVM system property or environment variable; the environment on iOS. */
internal expect fun setting(name: String): String?


actual val defaultServerAddress: ServerAddress =
    ServerAddress.resolve(setting("GEMSTONE_SERVER_HOST"), setting("GEMSTONE_SERVER_PORT"))


actual val defaultApiKey: String? = setting("GEMSTONE_API_KEY")


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
