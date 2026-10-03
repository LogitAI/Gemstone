package gemstone.framework.network.http

import gemstone.framework.network.ServerAddress
import io.ktor.client.*


expect val defaultServerAddress: ServerAddress


/** `GEMSTONE_API_KEY`, or null when unset. */
expect val defaultApiKey: String?


expect object HttpClientFactory {
    fun create(): HttpClient
}
