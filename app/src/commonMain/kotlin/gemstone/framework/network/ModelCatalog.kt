package gemstone.framework.network

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive


/** One model offered by the server. [id] is the server id (lowercase, e.g. `qwen3`). */
data class ModelInfo(val id: String, val name: String, val description: String)


/**
 * The server's `GET /api/models` answer. The `default` alias is not listed in [models]; it only
 * decides [defaultId] (the id of the model it points at, matched by `model_name`).
 */
data class ModelCatalog(val models: List<ModelInfo>, val defaultId: String?) {
    companion object {
        const val DEFAULT_ALIAS = "default"

        /** Parses `{ "<id>": {"model_name": ..., "model_description": ...}, ... }`. Throws on invalid JSON. */
        fun parse(body: String): ModelCatalog {
            val root = Json.parseToJsonElement(body).jsonObject
            fun JsonObject.text(key: String) = this[key]?.jsonPrimitive?.contentOrNull

            val all = root.entries.map { (id, value) ->
                val obj = value as? JsonObject ?: JsonObject(emptyMap())
                ModelInfo(id, obj.text("model_name") ?: id, obj.text("model_description") ?: "")
            }
            val models = all.filter { it.id != DEFAULT_ALIAS }
            val alias = all.firstOrNull { it.id == DEFAULT_ALIAS }
            val defaultId = (alias?.let { a -> models.firstOrNull { it.name == a.name } } ?: models.firstOrNull())?.id
            return ModelCatalog(models, defaultId)
        }
    }
}
