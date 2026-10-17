package gemstone.framework.network.http

import gemstone.framework.network.ServerSettings
import io.ktor.client.*


/** The platform's settings: what was set at launch, its preferences and its secure storage (SPEC S2.7). */
expect fun platformServerSettings(): ServerSettings


/** The server address and API key in effect; read on every request, so a saved change applies at once. */
val serverSettings: ServerSettings by lazy { platformServerSettings() }


expect object HttpClientFactory {
    fun create(): HttpClient
}
