package gemstone.framework.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue


class ModelCatalogTest {
    private val serverResponse = """
        {
          "qwen3": {"model_name": "Qwen3 0.6B", "model_description": "Small chat model"},
          "default": {"model_name": "Qwen3 0.6B", "model_description": "Small chat model"},
          "llama": {"model_name": "Llama 3.2 1B", "model_description": "From the HF cache", "extra": 1}
        }
    """.trimIndent()

    @Test
    fun parsesServerIdsNamesAndDescriptions() {
        val catalog = ModelCatalog.parse(serverResponse)
        assertEquals(listOf("qwen3", "llama"), catalog.models.map { it.id })
        assertEquals(ModelInfo("qwen3", "Qwen3 0.6B", "Small chat model"), catalog.models[0])
        assertEquals("Llama 3.2 1B", catalog.models[1].name)
    }

    @Test
    fun hidesDefaultAliasAndResolvesItToTheModelItPointsAt() {
        val catalog = ModelCatalog.parse(serverResponse)
        assertTrue(catalog.models.none { it.id == "default" })
        assertEquals("qwen3", catalog.defaultId)
    }

    @Test
    fun withoutMatchingAliasTargetDefaultIsTheFirstModel() {
        val catalog = ModelCatalog.parse(
            """{"a": {"model_name": "A", "model_description": ""}, "b": {"model_name": "B", "model_description": ""}}"""
        )
        assertEquals("a", catalog.defaultId)
    }

    @Test
    fun missingFieldsFallBackToTheId() {
        val catalog = ModelCatalog.parse("""{"x": {}}""")
        assertEquals(ModelInfo("x", "x", ""), catalog.models.single())
    }

    @Test
    fun emptyObjectGivesEmptyCatalog() {
        val catalog = ModelCatalog.parse("{}")
        assertTrue(catalog.models.isEmpty())
        assertNull(catalog.defaultId)
    }

    @Test
    fun invalidJsonThrows() {
        assertFailsWith<Exception> { ModelCatalog.parse("not json") }
    }
}
