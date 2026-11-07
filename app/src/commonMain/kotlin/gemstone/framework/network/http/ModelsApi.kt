package gemstone.framework.network.http

import gemstone.framework.network.ModelCatalog
import gemstone.framework.network.ServerAddress
import gemstone.framework.network.authorize
import io.ktor.client.request.*
import io.ktor.client.statement.*
import io.ktor.http.*
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put


class ModelsApi(
    private val address: () -> ServerAddress = { serverSettings.address },
    private val apiKey: () -> String? = { serverSettings.apiKey }
) {
    private val httpClient = HttpClientFactory.create()

    /** Fetches `GET /api/models`; failure (server down, bad status, bad JSON) is returned, not thrown. */
    suspend fun fetch(): Result<ModelCatalog> = try {
        val response = httpClient.get(address().httpUrl("/api/models")) { authorize(apiKey()) }
        if (response.status == HttpStatusCode.OK) {
            Result.success(ModelCatalog.parse(response.bodyAsText()))
        } else {
            Result.failure(Exception("Failed to load models: ${response.status}"))
        }
    } catch (e: Exception) {
        Result.failure(e)
    }

    /** Downloads a model via `POST /api/pull` (`stream: false`); returns failure instead of throwing. */
    suspend fun pull(name: String): Result<Unit> = try {
        val body = buildJsonObject {
            put("name", name)
            put("stream", false)
        }.toString()
        val response = httpClient.post(address().httpUrl("/api/pull")) {
            authorize(apiKey())
            contentType(ContentType.Application.Json)
            setBody(body)
        }
        if (response.status == HttpStatusCode.OK) {
            Result.success(Unit)
        } else {
            val detail = runCatching {
                Json.parseToJsonElement(response.bodyAsText()).jsonObject["error"]?.jsonPrimitive?.content
            }.getOrNull() ?: response.status.toString()
            Result.failure(Exception(detail))
        }
    } catch (e: Exception) {
        Result.failure(e)
    }
}
