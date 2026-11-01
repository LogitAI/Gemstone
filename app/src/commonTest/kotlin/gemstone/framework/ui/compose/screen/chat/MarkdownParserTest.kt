package gemstone.framework.ui.compose.screen.chat

import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue


/** SPEC S2.3 (#204): headings, lists, links, fenced code blocks and tables render while streaming. */
class MarkdownParserTest {

    @Test
    fun parsesHeadingsOneThroughSixAndStripsHashes() {
        val blocks = parseMarkdownBlocks("# Title\n## Section\n### Sub")
        assertEquals(
            listOf(
                MarkdownBlock.Heading(level = 1, text = "Title"),
                MarkdownBlock.Heading(level = 2, text = "Section"),
                MarkdownBlock.Heading(level = 3, text = "Sub"),
            ),
            blocks
        )
        val styled = buildAnnotatedStringFromMarkdown("# Title\nPlain line")
        assertEquals("Title\nPlain line", styled.text)
        assertTrue(styled.spanStyles.any { it.item.fontWeight == FontWeight.Bold })
    }

    @Test
    fun parsesUnorderedAndOrderedLists() {
        val md = "- First item\n* Second item\n1. Number one\n2. Number two"
        val blocks = parseMarkdownBlocks(md)
        assertEquals(
            listOf(
                MarkdownBlock.ListItem(ordered = false, marker = "•", text = "First item"),
                MarkdownBlock.ListItem(ordered = false, marker = "•", text = "Second item"),
                MarkdownBlock.ListItem(ordered = true, marker = "1.", text = "Number one"),
                MarkdownBlock.ListItem(ordered = true, marker = "2.", text = "Number two"),
            ),
            blocks
        )
        val styled = buildAnnotatedStringFromMarkdown(md)
        assertTrue("• First item" in styled.text)
        assertTrue("1. Number one" in styled.text)
    }

    @Test
    fun parsesInlineBoldItalicCodeAndLinks() {
        val styled: AnnotatedString = parseInlineMarkdown("Read **bold**, *italic*, `code`, and [Gemstone](https://example.com)!")
        assertEquals("Read bold, italic, code, and Gemstone!", styled.text)
        assertTrue(styled.spanStyles.any { it.item.fontWeight == FontWeight.Bold })
        assertTrue(styled.spanStyles.any { it.item.fontStyle == FontStyle.Italic })
        assertTrue(styled.spanStyles.any { it.item.fontFamily == FontFamily.Monospace })
        val links = styled.getLinkAnnotations(0, styled.length)
        assertEquals(1, links.size)
        val url = assertIs<LinkAnnotation.Url>(links.single().item)
        assertEquals("https://example.com", url.url)
        assertEquals("Gemstone", styled.text.substring(links.single().start, links.single().end))
    }

    @Test
    fun parsesFencedCodeBlocksWithLanguageAndContent() {
        val md = "Before\n```kotlin\nval x = 1\nprintln(x)\n```\nAfter"
        val blocks = parseMarkdownBlocks(md)
        assertEquals(
            listOf(
                MarkdownBlock.Paragraph("Before"),
                MarkdownBlock.CodeBlock(language = "kotlin", code = "val x = 1\nprintln(x)"),
                MarkdownBlock.Paragraph("After"),
            ),
            blocks
        )
    }

    @Test
    fun parsesPipeTablesAndSkipsAlignmentSeparatorRow() {
        val md = "| Name | Size |\n| :--- | ---: |\n| SmolLM2 | 135M |\n| Qwen3 | 0.6B |"
        val blocks = parseMarkdownBlocks(md)
        val table = assertIs<MarkdownBlock.Table>(blocks.single())
        assertEquals(listOf("Name", "Size"), table.headers)
        assertEquals(listOf(listOf("SmolLM2", "135M"), listOf("Qwen3", "0.6B")), table.rows)
    }

    @Test
    fun midConstructStreamingRendersWithoutErrors() {
        val partials = listOf(
            "#",
            "# Heading in prog",
            "Use **unclosed bold and *unclosed italic and `unclosed code",
            "Check [unclosed link](https://exam",
            "Check [unclosed label",
            "```python\ndef hello():\n    return 42",
            "| Col A | Col B\n| --- |",
            "| Col A | Col B |\n| val 1",
        )
        for (partial in partials) {
            val blocks = parseMarkdownBlocks(partial)
            assertTrue(blocks.isNotEmpty(), "expected blocks for: $partial")
            val rendered = buildAnnotatedStringFromMarkdown(partial)
            assertTrue(rendered.text.isNotEmpty(), "expected rendered text for: $partial")
        }
    }
}
