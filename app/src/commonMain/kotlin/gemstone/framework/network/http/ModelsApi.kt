package gemstone.framework.network.http

import gemstone.framework.network.ModelCatalog
import io.ktor.client.request.*
import io.ktor.client.statement.*
import io.ktor.http.*


class ModelsApi(private val serverUrl: String = defaultServerHost) {
    private val httpClient = HttpClientFactory.create()

    /** Fetches `GET /api/models`; failure (server down, bad status, bad JSON) is returned, not thrown. */
    suspend fun fetch(): Result<ModelCatalog> = try {
        val response = httpClient.get("http://$serverUrl/api/models")
        if (response.status == HttpStatusCode.OK) {
            Result.success(ModelCatalog.parse(response.bodyAsText()))
        } else {
            Result.failure(Exception("Failed to load models: ${response.status}"))
        }
    } catch (e: Exception) {
        Result.failure(e)
    }
}
