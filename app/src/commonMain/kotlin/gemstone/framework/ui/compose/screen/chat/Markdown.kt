package gemstone.framework.ui.compose.screen.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.*
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import gemstone.framework.ui.compose.theme.SuiteFontFamily


/**
 * Block-level Markdown constructs rendered in assistant replies (SPEC S2.3, #204).
 */
sealed interface MarkdownBlock {
    data class Heading(val level: Int, val text: String) : MarkdownBlock
    data class ListItem(val ordered: Boolean, val marker: String, val text: String) : MarkdownBlock
    data class CodeBlock(val language: String, val code: String) : MarkdownBlock
    data class Table(val headers: List<String>, val rows: List<List<String>>) : MarkdownBlock
    data class Paragraph(val text: String) : MarkdownBlock
}

private val headingRegex = Regex("""^(#{1,6})(?:\s+(.*))?$""")
private val unorderedListRegex = Regex("""^\s*[-*+]\s+(.*)$""")
private val orderedListRegex = Regex("""^\s*(\d+\.)\s+(.*)$""")
private val tableSeparatorCellRegex = Regex("""^:?-{2,}:?$""")

private fun isTableLine(line: String): Boolean {
    val trimmed = line.trim()
    return trimmed.startsWith("|") && trimmed.count { it == '|' } >= 2
}

private fun parseTableCells(line: String): List<String> {
    val trimmed = line.trim().removePrefix("|").removeSuffix("|")
    return trimmed.split("|").map { it.trim() }
}

private fun isTableSeparatorLine(line: String): Boolean {
    if (!isTableLine(line)) return false
    val cells = parseTableCells(line)
    return cells.isNotEmpty() && cells.all { tableSeparatorCellRegex.matches(it) }
}

/**
 * Parse a full or partially streamed Markdown reply into block-level constructs.
 * Unclosed code fences or incomplete table rows are tolerated so streaming never fails.
 */
fun parseMarkdownBlocks(markdown: String): List<MarkdownBlock> {
    if (markdown.isEmpty()) return emptyList()
    val lines = markdown.split("\n")
    val blocks = mutableListOf<MarkdownBlock>()
    val paragraphLines = mutableListOf<String>()

    fun flushParagraph() {
        if (paragraphLines.isNotEmpty()) {
            val text = paragraphLines.joinToString("\n").trimEnd()
            if (text.isNotEmpty()) {
                blocks.add(MarkdownBlock.Paragraph(text))
            }
            paragraphLines.clear()
        }
    }

    var i = 0
    while (i < lines.size) {
        val line = lines[i]
        val trimmed = line.trimStart()

        if (trimmed.startsWith("```")) {
            flushParagraph()
            val lang = trimmed.removePrefix("```").trim()
            val codeLines = mutableListOf<String>()
            i++
            while (i < lines.size && !lines[i].trimStart().startsWith("```")) {
                codeLines.add(lines[i])
                i++
            }
            if (i < lines.size && lines[i].trimStart().startsWith("```")) {
                i++
            }
            blocks.add(MarkdownBlock.CodeBlock(language = lang, code = codeLines.joinToString("\n")))
            continue
        }

        if (isTableLine(line)) {
            flushParagraph()
            val tableLines = mutableListOf<String>()
            while (i < lines.size && (isTableLine(lines[i]) || lines[i].trim().startsWith("|"))) {
                tableLines.add(lines[i])
                i++
            }
            val headers = parseTableCells(tableLines.first())
            val bodyStart = if (tableLines.size >= 2 && isTableSeparatorLine(tableLines[1])) 2 else 1
            val rows = tableLines.drop(bodyStart)
                .filter { it.trim().removePrefix("|").removeSuffix("|").isNotBlank() && !isTableSeparatorLine(it) }
                .map { parseTableCells(it) }
            blocks.add(MarkdownBlock.Table(headers = headers, rows = rows))
            continue
        }

        val headingMatch = headingRegex.matchEntire(line.trimEnd())
        if (headingMatch != null) {
            flushParagraph()
            val level = headingMatch.groupValues[1].length
            val text = headingMatch.groupValues[2].trim()
            blocks.add(MarkdownBlock.Heading(level = level, text = text))
            i++
            continue
        }

        val unorderedMatch = unorderedListRegex.matchEntire(line)
        if (unorderedMatch != null) {
            flushParagraph()
            blocks.add(MarkdownBlock.ListItem(ordered = false, marker = "•", text = unorderedMatch.groupValues[1]))
            i++
            continue
        }

        val orderedMatch = orderedListRegex.matchEntire(line)
        if (orderedMatch != null) {
            flushParagraph()
            blocks.add(
                MarkdownBlock.ListItem(
                    ordered = true,
                    marker = orderedMatch.groupValues[1],
                    text = orderedMatch.groupValues[2]
                )
            )
            i++
            continue
        }

        if (line.isBlank()) {
            flushParagraph()
        } else {
            paragraphLines.add(line)
        }
        i++
    }
    flushParagraph()
    return blocks
}

private val inlineTokenRegex = Regex(
    """`([^`\n]+)`|\[([^\]\n]+)]\(([^)\n]+)\)|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*"""
)

/**
 * Parse inline Markdown (`**bold**`, `*italic*`, `` `code` ``, `[label](url)`) into [builder].
 * Unclosed markers are emitted as plain text so mid-stream text never fails.
 */
fun parseInlineMarkdownInto(
    builder: AnnotatedString.Builder,
    text: String,
    linkColor: Color = Color(0xFF1A73E8)
) {
    var lastIndex = 0
    for (match in inlineTokenRegex.findAll(text)) {
        if (match.range.first > lastIndex) {
            builder.append(text.substring(lastIndex, match.range.first))
        }
        val codeContent = match.groups[1]?.value
        val linkLabel = match.groups[2]?.value
        val linkUrl = match.groups[3]?.value
        val boldContent = match.groups[4]?.value
        val italicContent = match.groups[5]?.value

        when {
            codeContent != null -> {
                builder.pushStyle(
                    SpanStyle(
                        fontFamily = FontFamily.Monospace,
                        background = Color.Gray.copy(alpha = 0.2f)
                    )
                )
                builder.append(codeContent)
                builder.pop()
            }
            linkLabel != null && linkUrl != null -> {
                builder.pushLink(
                    LinkAnnotation.Url(
                        url = linkUrl.trim(),
                        styles = TextLinkStyles(
                            style = SpanStyle(
                                color = linkColor,
                                textDecoration = TextDecoration.Underline,
                                fontWeight = FontWeight.Normal
                            )
                        )
                    )
                )
                builder.append(linkLabel)
                builder.pop()
            }
            boldContent != null -> {
                builder.pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                builder.append(boldContent)
                builder.pop()
            }
            italicContent != null -> {
                builder.pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                builder.append(italicContent)
                builder.pop()
            }
        }
        lastIndex = match.range.last + 1
    }
    if (lastIndex < text.length) {
        builder.append(text.substring(lastIndex))
    }
}

fun parseInlineMarkdown(text: String, linkColor: Color = Color(0xFF1A73E8)): AnnotatedString =
    buildAnnotatedString { parseInlineMarkdownInto(this, text, linkColor) }

/**
 * Render all blocks of [markdown] into a single [AnnotatedString] (used by tests and inline views).
 */
fun buildAnnotatedStringFromMarkdown(markdown: String, linkColor: Color = Color(0xFF1A73E8)): AnnotatedString =
    buildAnnotatedString {
        val blocks = parseMarkdownBlocks(markdown)
        blocks.forEachIndexed { index, block ->
            if (index > 0) append("\n")
            when (block) {
                is MarkdownBlock.Heading -> {
                    val size = when (block.level) {
                        1 -> 22.sp
                        2 -> 19.sp
                        3 -> 17.sp
                        else -> 15.sp
                    }
                    pushStyle(SpanStyle(fontWeight = FontWeight.Bold, fontSize = size))
                    if (block.text.isNotEmpty()) {
                        parseInlineMarkdownInto(this, block.text, linkColor)
                    } else {
                        append("#".repeat(block.level))
                    }
                    pop()
                }
                is MarkdownBlock.ListItem -> {
                    append("${block.marker} ")
                    parseInlineMarkdownInto(this, block.text, linkColor)
                }
                is MarkdownBlock.CodeBlock -> {
                    pushStyle(
                        SpanStyle(
                            fontFamily = FontFamily.Monospace,
                            background = Color.Gray.copy(alpha = 0.15f)
                        )
                    )
                    append(block.code.ifEmpty { " " })
                    pop()
                }
                is MarkdownBlock.Table -> {
                    val allRows = listOf(block.headers) + block.rows
                    allRows.forEachIndexed { rIdx, row ->
                        if (rIdx > 0) append("\n")
                        row.forEachIndexed { cIdx, cell ->
                            if (cIdx > 0) append(" | ")
                            if (rIdx == 0) pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                            parseInlineMarkdownInto(this, cell, linkColor)
                            if (rIdx == 0) pop()
                        }
                    }
                }
                is MarkdownBlock.Paragraph -> {
                    parseInlineMarkdownInto(this, block.text, linkColor)
                }
            }
        }
    }

@Composable
fun MarkdownBlocksView(
    blocks: List<MarkdownBlock>,
    textColor: Color = MaterialTheme.colorScheme.primary,
    trailingCursor: String? = null
) {
    val fontFamily = SuiteFontFamily
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        blocks.forEachIndexed { index, block ->
            val isLast = index == blocks.lastIndex
            val suffix = if (isLast && trailingCursor != null) trailingCursor else ""
            when (block) {
                is MarkdownBlock.Heading -> {
                    val fontSize = when (block.level) {
                        1 -> 22.sp
                        2 -> 19.sp
                        3 -> 17.sp
                        else -> 16.sp
                    }
                    val styled = buildAnnotatedString {
                        parseInlineMarkdownInto(this, block.text.ifEmpty { "#".repeat(block.level) })
                        if (suffix.isNotEmpty()) append(suffix)
                    }
                    Text(
                        text = styled,
                        fontFamily = fontFamily,
                        fontWeight = FontWeight.Bold,
                        fontSize = fontSize,
                        color = textColor,
                        lineHeight = (fontSize.value + 6f).sp
                    )
                }
                is MarkdownBlock.ListItem -> {
                    val styled = buildAnnotatedString {
                        pushStyle(SpanStyle(fontWeight = FontWeight.Medium))
                        append("${block.marker} ")
                        pop()
                        parseInlineMarkdownInto(this, block.text)
                        if (suffix.isNotEmpty()) append(suffix)
                    }
                    Text(
                        text = styled,
                        fontFamily = fontFamily,
                        fontWeight = FontWeight.Light,
                        fontSize = 15.sp,
                        color = textColor,
                        lineHeight = 22.sp,
                        modifier = Modifier.padding(start = 6.dp)
                    )
                }
                is MarkdownBlock.CodeBlock -> {
                    Surface(
                        color = Color(0xFFF1F1F1),
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(modifier = Modifier.padding(10.dp)) {
                            if (block.language.isNotEmpty()) {
                                Text(
                                    text = block.language,
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Medium,
                                    color = Color.Gray,
                                    modifier = Modifier.padding(bottom = 4.dp)
                                )
                            }
                            Text(
                                text = block.code + suffix,
                                fontFamily = FontFamily.Monospace,
                                fontSize = 13.sp,
                                color = textColor,
                                lineHeight = 19.sp,
                                modifier = Modifier.horizontalScroll(rememberScrollState())
                            )
                        }
                    }
                }
                is MarkdownBlock.Table -> {
                    MarkdownTableView(block, textColor = textColor, fontFamily = fontFamily)
                }
                is MarkdownBlock.Paragraph -> {
                    val styled = buildAnnotatedString {
                        parseInlineMarkdownInto(this, block.text)
                        if (suffix.isNotEmpty()) append(suffix)
                    }
                    Text(
                        text = styled,
                        fontFamily = fontFamily,
                        fontWeight = FontWeight.Light,
                        fontSize = 15.sp,
                        color = textColor,
                        lineHeight = 22.sp
                    )
                }
            }
        }
    }
}

@Composable
private fun MarkdownTableView(
    table: MarkdownBlock.Table,
    textColor: Color,
    fontFamily: FontFamily
) {
    val colCount = maxOf(table.headers.size, table.rows.maxOfOrNull { it.size } ?: 0)
    if (colCount == 0) return
    val borderColor = Color.LightGray.copy(alpha = 0.7f)

    Column(
        modifier = Modifier
            .horizontalScroll(rememberScrollState())
            .border(1.dp, borderColor, RoundedCornerShape(6.dp))
    ) {
        Row(
            modifier = Modifier
                .background(Color(0xFFF3F3F3), RoundedCornerShape(topStart = 6.dp, topEnd = 6.dp))
                .padding(vertical = 6.dp, horizontal = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            for (c in 0 until colCount) {
                val cell = table.headers.getOrNull(c).orEmpty()
                Text(
                    text = parseInlineMarkdown(cell),
                    fontFamily = fontFamily,
                    fontWeight = FontWeight.Bold,
                    fontSize = 14.sp,
                    color = textColor,
                    modifier = Modifier.widthIn(min = 64.dp)
                )
            }
        }
        table.rows.forEach { row ->
            HorizontalDivider(thickness = 0.8.dp, color = borderColor)
            Row(
                modifier = Modifier.padding(vertical = 6.dp, horizontal = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                for (c in 0 until colCount) {
                    val cell = row.getOrNull(c).orEmpty()
                    Text(
                        text = parseInlineMarkdown(cell),
                        fontFamily = fontFamily,
                        fontWeight = FontWeight.Light,
                        fontSize = 14.sp,
                        color = textColor,
                        modifier = Modifier.widthIn(min = 64.dp)
                    )
                }
            }
        }
    }
}
