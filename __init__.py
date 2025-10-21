"""Simple Markdown-on-paste add-on for Anki.

When the user pastes plain text that looks like Markdown into the Anki editor,
we convert it to HTML before Anki inserts it into the field. Rich-text pastes
or internal Anki pastes are left untouched.
"""

from __future__ import annotations

import html
import itertools
import re
from typing import Callable

from aqt import gui_hooks
from aqt.qt import QMimeData

try:
    import markdown  # type: ignore
except ImportError:
    markdown = None


MARKDOWN_CLUES = (
    r"^#{1,6}\s",  # headings
    r"^\s{0,3}[-*+]\s",  # unordered list
    r"^\s{0,3}\d+[.)]\s",  # ordered list
    r"`{1,3}[^`]+`{1,3}",  # inline/code fences
    r"\[[^\]]+\]\([^)]+\)",  # links
    r"\*\*[^*]+\*\*",  # bold
    r"__[^_]+__",  # bold
    r"^\s{0,3}>\s",  # blockquote
)


def _looks_like_markdown(text: str) -> bool:
    """Heuristic to decide if plain text is Markdown."""
    stripped = text.strip()
    if not stripped:
        return False
    if "\n" not in stripped and len(stripped) < 6:
        return False
    for pattern in MARKDOWN_CLUES:
        if re.search(pattern, stripped, re.MULTILINE):
            return True
    return False


def _convert_markdown(text: str) -> str:
    """Convert Markdown to HTML using the markdown package if available."""
    if markdown:
        try:
            return markdown.markdown(
                text,
                extensions=[
                    "fenced_code",
                    "tables",
                    "smarty",
                ],
                output_format="html5",
            )
        except Exception:
            # fall back to local converter
            pass
    return _basic_markdown_to_html(text)


def _basic_markdown_to_html(text: str) -> str:
    """Minimal Markdown-to-HTML converter covering common constructs."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    html_lines: list[str] = []
    in_code_block = False
    code_block_language = ""
    list_stack: list[str] = []
    paragraph_lines: list[str] = []

    def close_lists() -> None:
        while list_stack:
            html_lines.append(f"</{list_stack.pop()}>")

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            paragraph = " ".join(line.strip() for line in paragraph_lines).strip()
            if paragraph:
                html_lines.append(f"<p>{_process_inline(paragraph)}</p>")
            paragraph_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.lstrip()

        if stripped.startswith("```"):
            if in_code_block:
                html_lines.append("</code></pre>")
                in_code_block = False
                code_block_language = ""
            else:
                flush_paragraph()
                close_lists()
                in_code_block = True
                code_block_language = stripped[3:].strip()
                class_attr = (
                    f' class="language-{html.escape(code_block_language)}"'
                    if code_block_language
                    else ""
                )
                html_lines.append(f"<pre><code{class_attr}>")
            continue

        if in_code_block:
            html_lines.append(f"{html.escape(raw_line)}")
            continue

        if not stripped:
            flush_paragraph()
            close_lists()
            continue

        if stripped in {"---", "***", "___"}:
            flush_paragraph()
            close_lists()
            html_lines.append("<hr />")
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if heading:
            flush_paragraph()
            close_lists()
            level = min(len(heading.group(1)), 6)
            content = heading.group(2).strip()
            html_lines.append(f"<h{level}>{_process_inline(content)}</h{level}>")
            continue

        blockquote = re.match(r"^>\s?(.*)", stripped)
        if blockquote:
            flush_paragraph()
            close_lists()
            html_lines.append(f"<blockquote>{_process_inline(blockquote.group(1))}</blockquote>")
            continue

        unordered = re.match(r"^[-*+]\s+(.*)", stripped)
        if unordered:
            flush_paragraph()
            if not list_stack or list_stack[-1] != "ul":
                close_lists()
                list_stack.append("ul")
                html_lines.append("<ul>")
            html_lines.append(f"<li>{_process_inline(unordered.group(1).strip())}</li>")
            continue

        ordered = re.match(r"^\d+[.)]\s+(.*)", stripped)
        if ordered:
            flush_paragraph()
            if not list_stack or list_stack[-1] != "ol":
                close_lists()
                list_stack.append("ol")
                html_lines.append("<ol>")
            html_lines.append(f"<li>{_process_inline(ordered.group(1).strip())}</li>")
            continue

        paragraph_lines.append(stripped)

    flush_paragraph()
    close_lists()

    if in_code_block:
        html_lines.append("</code></pre>")

    return "\n".join(html_lines)


def _process_inline(text: str) -> str:
    """Handle inline Markdown formatting in a conservative way."""
    placeholders: dict[str, str] = {}
    counter = itertools.count()

    def store(replacement: str) -> str:
        token = f"@@MD{next(counter)}@@"
        placeholders[token] = replacement
        return token

    def replace(pattern: str, func: Callable[[re.Match[str]], str], input_text: str) -> str:
        return re.sub(pattern, func, input_text)

    # Handle code spans and links first so we can keep them safe during escaping.
    text = replace(
        r"`([^`]+)`",
        lambda m: store(f"<code>{html.escape(m.group(1))}</code>"),
        text,
    )
    text = replace(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: store(
            f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'
        ),
        text,
    )

    escaped = html.escape(text, quote=False)

    escaped = replace(
        r"\*\*([^*]+)\*\*",
        lambda m: f"<strong>{m.group(1)}</strong>",
        escaped,
    )
    escaped = replace(
        r"__([^_]+)__",
        lambda m: f"<strong>{m.group(1)}</strong>",
        escaped,
    )
    escaped = replace(
        r"(?<!\*)\*([^*]+)\*(?!\*)",
        lambda m: f"<em>{m.group(1)}</em>",
        escaped,
    )
    escaped = replace(
        r"(?<!_)_([^_]+)_(?!_)",
        lambda m: f"<em>{m.group(1)}</em>",
        escaped,
    )

    for token, value in placeholders.items():
        escaped = escaped.replace(html.escape(token), value)
        escaped = escaped.replace(token, value)

    return escaped


def _maybe_convert_markdown(
    mime: QMimeData,
    editor_web_view,
    internal: bool,
    extended: bool,
    drop_event: bool,
) -> QMimeData:
    if internal:
        return mime
    if mime is None:
        return mime
    if mime.hasHtml():
        return mime
    if not mime.hasText():
        return mime

    text = mime.text()
    if not _looks_like_markdown(text):
        return mime

    try:
        html_text = _convert_markdown(text)
    except Exception:
        return mime

    if not html_text.strip():
        return mime

    converted = QMimeData()
    converted.setHtml(html_text)
    converted.setText(text)
    return converted


gui_hooks.editor_will_process_mime.append(_maybe_convert_markdown)
