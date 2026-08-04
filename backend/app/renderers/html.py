"""Printable HTML for a PRISM document.

``render_print_html(markdown_text, title, estimate)`` returns one self-contained
A4 page: a small dependency-free markdown converter plus an embedded stylesheet
built from docs/DESIGN.md. There is no PDF library in this project - the browser
print dialog is the PDF writer, so the print rules below are the product.

Deliberate constraints:

* Only the six DESIGN.md colours appear. No dark mode - a quotation pad is paper.
* The duplicate tint is applied as a *border*, not a background, because browsers
  omit background graphics from print by default but always draw borders. The
  developer sheet therefore keeps its green left edge on paper, which is the
  whole point of the band.
* Fonts load from Google Fonts with a serif / sans / monospace fallback stack, so
  an offline print is still correctly proportioned.
"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from app import kinds
from app.schemas import Estimate

from .markdown import quotation_reference
from app.design import resolve as resolve_design

from .money import normalise_code

__all__ = ["render_print_html", "markdown_to_html"]


#: The printable page loads the same face as the app. A quotation that arrives
#: set in a different font from the screen it was prepared on reads as a
#: different document from a different company.
FONT_HREF = (
    "https://fonts.googleapis.com/css2"
    "?family=Figtree:ital,wght@0,300..900;1,300..900"
    "&display=swap"
)

LEFT, RIGHT, CENTRE = "left", "right", "center"


# --- escaping ----------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _escape(text: str) -> str:
    """Escape every HTML-significant character. Runs before any conversion."""
    return (
        _CONTROL.sub("", str(text))
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# --- inline markdown ---------------------------------------------------------

_CODE_SPAN = re.compile(r"(`+)([^\n]+?)\1")
_LINK = re.compile(r"\[([^\]\n]*)\]\(([^)\s]*)\)")
_AUTO_LINK = re.compile(r"(?<![\w\">=/])(https?://[^\s<>\"')]+)")
_STRONG_STAR = re.compile(r"\*\*(?=\S)([^\n]+?)(?<=\S)\*\*")
_STRONG_UNDER = re.compile(r"(?<![\w\\_])__(?=\S)([^\n]+?)(?<=\S)__(?![\w_])")
_EM_STAR = re.compile(r"(?<![\w*\\])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_EM_UNDER = re.compile(r"(?<![\w\\_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)([^\n]+?)(?<=\S)~~")
_BACKSLASH = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~\"'])")
#: Markdown escapes whose target already turned into an entity during HTML
#: escaping - "\<" arrives here as "\&lt;" and must lose only the backslash.
_BACKSLASH_ENTITY = re.compile(r"\\(&(?:lt|gt|amp|quot|#39);)")
_PLACEHOLDER = re.compile(r"\x01(\d+)\x01")
_SAFE_HREF = re.compile(r"^(?:https?://|mailto:|tel:|#|/|\.{0,2}/)[^\s]*$", re.I)
_BARE_PATH = re.compile(r"^[\w][\w./?=&%+#~-]*$")


def _safe_href(raw: str) -> str:
    href = raw.strip()
    if not href or any(ch.isspace() for ch in href) or "&quot;" in href:
        return ""
    if _SAFE_HREF.match(href) or _BARE_PATH.match(href):
        return href
    return ""


def _inline(raw: str) -> str:
    """Convert one run of inline markdown. Escapes HTML first, always."""
    text = _escape(raw)
    store: List[str] = []

    def stash(html: str) -> str:
        store.append(html)
        return f"\x01{len(store) - 1}\x01"

    def code_sub(match: re.Match) -> str:
        return stash(f"<code>{match.group(2).strip()}</code>")

    text = _CODE_SPAN.sub(code_sub, text)

    def link_sub(match: re.Match) -> str:
        label = match.group(1)
        href = _safe_href(match.group(2))
        if not href:
            return label or match.group(2)
        return stash(f'<a href="{href}">{label or href}</a>')

    text = _LINK.sub(link_sub, text)
    text = _AUTO_LINK.sub(lambda m: stash(f'<a href="{m.group(1)}">{m.group(1)}</a>'), text)

    text = _STRONG_STAR.sub(r"<strong>\1</strong>", text)
    text = _STRONG_UNDER.sub(r"<strong>\1</strong>", text)
    text = _EM_STAR.sub(r"<em>\1</em>", text)
    text = _EM_UNDER.sub(r"<em>\1</em>", text)
    text = _STRIKE.sub(r"<del>\1</del>", text)
    text = _BACKSLASH_ENTITY.sub(r"\1", text)
    text = _BACKSLASH.sub(r"\1", text)

    return _PLACEHOLDER.sub(lambda m: store[int(m.group(1))], text)


_MARKUP_STRIP = re.compile(r"[*`~]|\\(?=.)")


def _plain(raw: str) -> str:
    """The visible text of a markdown fragment, used for classifying cells."""
    return _MARKUP_STRIP.sub("", raw).strip()


# --- numeric cell detection --------------------------------------------------

_HAS_DIGIT = re.compile(r"\d")
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
_NUMBER_TOKEN = re.compile(r"^\d[\d,./:%×x+-]*$")
_TOKEN_EDGE = "(),.;:!?[]{}\"'‘’“”–—-"
_SYMBOL_EDGE = "₱$£€¥₩₫฿₹₺₽₪₴৳₦+-"
#: Words that may sit beside a number without making the cell prose.
_FIGURE_WORDS = frozenset(
    {
        "hour", "hours", "day", "days", "week", "weeks", "month", "months",
        "year", "years", "item", "items", "lump", "sum", "sums", "each", "per",
        "and", "to", "x", "pcs", "no", "of",
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
    }
)


def _is_figure(text: str) -> bool:
    """True when a table cell should be set in DM Mono.

    Every cell that is a figure - money, quantity, percentage, duration, date,
    reference, ID, bare currency code - reads as mono so the columns align. A
    sentence that merely mentions a number stays in the serif body face, because
    a paragraph set in mono is a typesetting bug, not a design decision.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if _CURRENCY_CODE.match(stripped):
        return True
    if not _HAS_DIGIT.search(stripped):
        return False

    letters = sum(1 for ch in stripped if ch.isalpha())
    digits = sum(1 for ch in stripped if ch.isdigit())
    if digits >= letters:
        return True

    for token in stripped.split():
        core = token.strip(_TOKEN_EDGE)
        if not core:
            continue
        if _NUMBER_TOKEN.match(core) or _NUMBER_TOKEN.match(core.lstrip(_SYMBOL_EDGE)):
            continue
        if core.lower() in _FIGURE_WORDS:
            continue
        return False
    return True


# --- block markdown ----------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s{0,3}(?:-{3,}|_{3,}|\*{3,})\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)\s*$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_UL_ITEM = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_OL_ITEM = re.compile(r"^(\s*)(\d{1,9})[.)]\s+(.*)$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_TABLE_DELIM = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def _is_list_item(line: str) -> bool:
    return bool(_UL_ITEM.match(line) or _OL_ITEM.match(line))


def _split_row(line: str) -> List[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip() for cell in _CELL_SPLIT.split(text)]


def _alignments(delimiter: str, columns: int) -> List[str]:
    aligns: List[str] = []
    for cell in _split_row(delimiter):
        starts = cell.startswith(":")
        ends = cell.endswith(":")
        if starts and ends:
            aligns.append(CENTRE)
        elif ends:
            aligns.append(RIGHT)
        else:
            aligns.append(LEFT)
    while len(aligns) < columns:
        aligns.append(LEFT)
    return aligns[:columns]


def _row_class(cells: Sequence[str]) -> str:
    plains = [_plain(cell) for cell in cells]
    filled = [index for index, value in enumerate(plains) if value]

    if not filled:
        return "is-spacer"

    first = plains[0].lower()
    if filled == [0] and cells[0].strip().startswith("**") and cells[0].strip().endswith("**"):
        return "is-group"
    if first.startswith("subtotal"):
        return "is-subtotal"
    if first.startswith("total"):
        return "is-total"
    return ""


def _render_table(header_cells: Sequence[str], aligns: Sequence[str], body: Sequence[Sequence[str]]) -> str:
    columns = len(aligns)
    has_header = any(_plain(cell) for cell in header_cells)

    def cell_html(tag: str, raw: str, index: int, row_class: str) -> str:
        text = _plain(raw)
        classes = []
        if _is_figure(text) and row_class != "is-group":
            classes.append("num")
        align = aligns[index] if index < len(aligns) else LEFT
        if align != LEFT:
            classes.append(f"al-{align}")
        attr = f' class="{" ".join(classes)}"' if classes else ""
        return f"<{tag}{attr}>{_inline(raw)}</{tag}>"

    parts: List[str] = []
    css_class = "grid" if has_header else "plain"
    parts.append(f'<div class="table-wrap"><table class="{css_class}">')

    if has_header:
        cells = list(header_cells) + [""] * (columns - len(header_cells))
        parts.append("<thead><tr>")
        parts.extend(cell_html("th", cells[index], index, "") for index in range(columns))
        parts.append("</tr></thead>")

    parts.append("<tbody>")
    for raw_row in body:
        cells = list(raw_row) + [""] * (columns - len(raw_row))
        cells = cells[:columns]
        row_class = _row_class(cells)
        attr = f' class="{row_class}"' if row_class else ""
        parts.append(f"<tr{attr}>")
        parts.extend(cell_html("td", cells[index], index, row_class) for index in range(columns))
        parts.append("</tr>")
    parts.append("</tbody></table></div>")

    return "".join(parts)


def _render_list(lines: Sequence[str]) -> str:
    """Render one list block, honouring indentation for nesting."""
    entries: List[Tuple[bool, str, List[str]]] = []
    base_indent = None
    ordered = False
    start = 1

    for line in lines:
        match = _OL_ITEM.match(line)
        is_ordered = bool(match)
        if not match:
            match = _UL_ITEM.match(line)

        if match:
            indent = len(match.group(1).expandtabs(4))
            if base_indent is None:
                base_indent = indent
                ordered = is_ordered
                if is_ordered:
                    try:
                        start = int(match.group(2))
                    except ValueError:
                        start = 1
            if indent <= base_indent:
                entries.append((is_ordered, match.group(3), []))
                continue

        if entries:
            entries[-1][2].append(line[base_indent + 2 :] if base_indent is not None else line.strip())
        # A stray continuation before any item is dropped: it cannot be attached
        # to anything and reproducing it would corrupt the list.

    if not entries:
        return ""

    tag = "ol" if ordered else "ul"
    open_tag = f'<ol start="{start}">' if ordered and start != 1 else f"<{tag}>"

    parts = [open_tag]
    for _, text, children in entries:
        task = _TASK.match(text.strip())
        classes = []
        content = text
        if task:
            classes.append("task")
            checked = task.group(1).lower() == "x"
            box = '<span class="chk chk-on" aria-hidden="true"></span>' if checked else '<span class="chk" aria-hidden="true"></span>'
            content = task.group(2)
            item_html = f"{box}<span>{_inline(content)}</span>"
        else:
            item_html = _inline(content)

        lead: List[str] = []
        nested: List[str] = []
        for child in children:
            if nested or _is_list_item(child):
                nested.append(child)
            elif child.strip():
                lead.append(child.strip())

        if lead:
            item_html += " " + _inline(" ".join(lead))

        child_html = _render_list([child for child in nested if child.strip()]) if nested else ""

        attr = f' class="{" ".join(classes)}"' if classes else ""
        parts.append(f"<li{attr}>{item_html}{child_html}</li>")
    parts.append(f"</{tag}>")
    return "".join(parts)


def _starts_table(lines: Sequence[str], index: int) -> bool:
    if "|" not in lines[index] or index + 1 >= len(lines):
        return False
    delimiter = lines[index + 1]
    return "|" in delimiter and bool(_TABLE_DELIM.match(delimiter))


def _breaks_paragraph(lines: Sequence[str], index: int) -> bool:
    """True when this line starts a new block and must not be swallowed."""
    line = lines[index]
    return bool(
        _HEADING.match(line)
        or _HR.match(line)
        or _FENCE.match(line)
        or _QUOTE.match(line)
        or _is_list_item(line)
        or _starts_table(lines, index)
    )


def markdown_to_html(source: str) -> str:
    """Convert GitHub-flavoured markdown to HTML. No external dependencies."""
    text = str(source or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out: List[str] = []
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]

        if not line.strip():
            index += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            body: List[str] = []
            index += 1
            while index < total and not re.match(rf"^\s{{0,3}}{marker}{{3,}}\s*$", lines[index]):
                body.append(lines[index])
                index += 1
            index += 1
            out.append(f"<pre><code>{_escape(chr(10).join(body))}</code></pre>")
            continue

        if _HR.match(line):
            out.append("<hr>")
            index += 1
            continue

        # Room to sign: an explicit gap rather than a paragraph containing a
        # space, which is what a signature block needs above a printed name.
        if line.strip() == "&nbsp;":
            out.append('<p class="sign-space">&nbsp;</p>')
            index += 1
            continue

        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if _starts_table(lines, index):
            header_cells = _split_row(line)
            aligns = _alignments(lines[index + 1], len(header_cells))
            index += 2
            body_rows: List[List[str]] = []
            while index < total and "|" in lines[index] and lines[index].strip():
                body_rows.append(_split_row(lines[index]))
                index += 1
            out.append(_render_table(header_cells, aligns, body_rows))
            continue

        if _QUOTE.match(line):
            inner: List[str] = []
            while index < total and lines[index].strip():
                match = _QUOTE.match(lines[index])
                if match is None and _breaks_paragraph(lines, index):
                    break
                inner.append(match.group(1) if match else lines[index].strip())
                index += 1
            out.append(f"<blockquote>{markdown_to_html(chr(10).join(inner))}</blockquote>")
            continue

        if _is_list_item(line):
            block: List[str] = []
            while index < total:
                current = lines[index]
                if _is_list_item(current):
                    block.append(current)
                    index += 1
                    continue
                if current.strip() and current[:1] in " \t":
                    block.append(current)
                    index += 1
                    continue
                if not current.strip():
                    look = index + 1
                    if look < total and (_is_list_item(lines[look]) or lines[look][:1] in " \t") and lines[look].strip():
                        index += 1
                        continue
                break
            out.append(_render_list(block))
            continue

        paragraph: List[str] = []
        while index < total and lines[index].strip():
            current = lines[index]
            if paragraph and _breaks_paragraph(lines, index):
                break
            hard_break = current.endswith("  ")
            paragraph.append(_inline(current.strip()) + ("<br>" if hard_break else ""))
            index += 1
        if paragraph:
            out.append("<p>" + " ".join(paragraph) + "</p>")
            continue

        index += 1

    return "\n".join(out)


# --- page shell --------------------------------------------------------------


_DUPLICATE_WORDS = ("requirement", "developer", "duplicate")


def _is_duplicate(title: str, kind: str, markdown_text: str) -> bool:
    """Which of the two sheets is this?

    ``kind`` when the caller passes it, otherwise the title, otherwise the
    document's own H1. Reading the H1 costs the markdown nothing - no marker is
    embedded in the deliverable - and it keeps the green edge band correct even
    when the caller supplies a terse title.
    """
    if kind:
        return any(word in kind.lower() for word in _DUPLICATE_WORDS)
    if title:
        return any(word in title.lower() for word in _DUPLICATE_WORDS)
    for line in str(markdown_text or "").split("\n"):
        if line.startswith("# "):
            return any(word in line.lower() for word in _DUPLICATE_WORDS)
        if line.strip():
            break
    return False


def _stylesheet() -> str:
    return """
:root{
  --ink:#1D1B17;
  --paper:#FFFDFA;
  --duplicate:#F1EDE5;
  --ballpoint:#35655A;
  --rule:#E4DED4;
  --void:#6B6459;
  --display:'Figtree','Helvetica Neue',Arial,sans-serif;
  --body:'Figtree','Helvetica Neue',Arial,sans-serif;
  --mono:'Figtree','Helvetica Neue',Arial,sans-serif;
}
*,*::before,*::after{box-sizing:border-box}
html{background:var(--paper);-webkit-text-size-adjust:100%}
body{
  margin:0;
  background:var(--paper);
  color:var(--ink);
  font-family:var(--body);
  font-size:15px;
  line-height:1.6;
  font-weight:400;
  font-variant-numeric:tabular-nums;
  text-rendering:optimizeLegibility;
}
.doc--duplicate{border-left:5mm solid var(--duplicate);padding-left:6mm}
.sheet{max-width:940px;margin:0 auto;padding:36px 28px 72px}

h1,h2,h3,h4,h5,h6{
  font-family:var(--display);
  font-weight:700;
  letter-spacing:-0.01em;
  color:var(--ink);
  margin:0 0 10px;
  line-height:1.15;
}
h1{font-size:34px;margin-bottom:18px}
h2{font-size:18px;margin-top:34px;padding-top:9px;border-top:1px solid var(--rule)}
h3{font-size:15px;margin-top:24px;letter-spacing:0}
h4,h5,h6{font-size:13px;margin-top:18px;letter-spacing:0;color:var(--void)}

p{margin:0 0 12px}
strong{font-weight:600}
em{font-style:italic}
del{text-decoration:line-through;color:var(--void)}
a{color:var(--ballpoint);text-decoration:underline;text-underline-offset:2px}
hr{border:0;border-top:1px solid var(--rule);margin:26px 0}
.sign-space{margin:0;height:26px}
/* The proposal ends its cover with a rule; on paper that is where the
   page turns. On screen it stays a rule, because a screen has no pages.

   The title sits down the page rather than at the top of it: a cover is a
   page whose only job is to say what this is and who it is for, and a
   heading tight under the letterhead reads as the first page of a report
   instead. On screen the offset is smaller - there is no page to fill. */
.doc--cover>hr:first-of-type{break-after:page;page-break-after:always}
.doc--cover>h1:first-of-type{margin-top:26mm}
@media print{
  .doc--cover>h1:first-of-type{margin-top:64mm}
}
blockquote{
  margin:14px 0;
  padding:2px 0 2px 14px;
  border-left:2px solid var(--ballpoint);
  color:var(--void);
  font-style:italic;
}
blockquote p:last-child{margin-bottom:0}
code{font-family:var(--mono);font-size:13px;color:var(--ink)}
pre{
  font-family:var(--mono);
  font-size:13px;
  border:1px solid var(--rule);
  padding:12px 14px;
  overflow-x:auto;
  margin:0 0 14px;
  line-height:1.5;
}
pre code{font-size:inherit}

ul,ol{margin:0 0 14px;padding-left:22px}
li{margin:0 0 5px}
li>ul,li>ol{margin-top:5px}
li.task{list-style:none;margin-left:-22px;padding-left:22px;position:relative}
.chk{
  position:absolute;
  left:0;
  top:0.34em;
  width:13px;
  height:13px;
  border:1.5px solid var(--void);
  border-radius:4px;
  background:var(--paper);
  box-sizing:border-box;
}
/* Print drops background colours by default but always draws borders, so the
   ticked state is a border-drawn check rather than a filled square. */
.chk-on{
  border-color:var(--ballpoint);
  background:var(--ballpoint);
}
.chk-on::after{
  content:"";
  position:absolute;
  left:3px;
  top:0px;
  width:4px;
  height:8px;
  border:solid var(--paper);
  border-width:0 2px 2px 0;
  transform:rotate(45deg);
}

.table-wrap{overflow-x:auto;margin:14px 0 20px}
table{width:100%;border-collapse:collapse;font-size:13px;line-height:1.45}
th{
  font-family:var(--display);
  font-weight:700;
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:0.02em;
  text-align:left;
  padding:7px 10px 6px;
  border-bottom:1.5px solid var(--ink);
  vertical-align:bottom;
  white-space:nowrap;
}
td{padding:6px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
td.num,th.num{
  font-family:var(--mono);
  font-size:13px;
  font-variant-numeric:tabular-nums;
  font-feature-settings:'tnum' 1;
  white-space:nowrap;
}
.al-right{text-align:right}
.al-center{text-align:center}

tr.is-spacer td{border-bottom:0;padding:0;height:12px}
tr.is-group td{
  font-family:var(--display);
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:0.04em;
  border-bottom:1px solid var(--ink);
  padding-top:16px;
  padding-bottom:4px;
}
tr.is-group td strong{font-weight:700}
tr.is-subtotal td{border-bottom:1px solid var(--rule);color:var(--void)}
tr.is-subtotal td.num{color:var(--ink)}
tr.is-total td{
  border-top:4px double var(--ballpoint);
  border-bottom:1px solid var(--ballpoint);
  color:var(--ballpoint);
  font-weight:600;
  padding-top:10px;
  padding-bottom:9px;
}
tr.is-total td.num{font-size:15px;font-weight:500;letter-spacing:0.01em}

table.plain{width:auto;max-width:100%}
table.plain td{border-bottom:1px dotted var(--rule);padding:5px 22px 5px 0}
table.plain td:first-child{
  font-family:var(--display);
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:0.03em;
  color:var(--void);
  white-space:nowrap;
}
table.plain td:first-child strong{font-weight:700}

.letterhead{
  display:flex;
  flex-wrap:wrap;
  align-items:baseline;
  justify-content:space-between;
  gap:8px 20px;
  border-bottom:2px solid var(--ink);
  padding-bottom:9px;
  margin-bottom:28px;
  font-family:var(--mono);
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:0.09em;
  color:var(--void);
}
.letterhead .brand{
  font-family:var(--display);
  font-weight:700;
  font-size:18px;
  letter-spacing:-0.01em;
  text-transform:uppercase;
  color:var(--ink);
}
.letterhead .ref{color:var(--ink);white-space:nowrap}
.colophon{
  margin-top:44px;
  padding-top:10px;
  border-top:1px solid var(--rule);
  font-family:var(--mono);
  font-size:12px;
  letter-spacing:0.07em;
  text-transform:uppercase;
  color:var(--void);
}
.empty{color:var(--void);font-style:italic}

@media (max-width:640px){
  .sheet{padding:24px 18px 48px}
  h1{font-size:24px}
  .letterhead{font-size:11px}
}

@page{size:A4;margin:18mm}

@media print{
  html,body{background:var(--paper)}
  body{font-size:14px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .sheet{max-width:none;margin:0;padding:0}
  .doc--duplicate{padding-left:5mm}
  .table-wrap{overflow:visible}
  thead{display:table-header-group}
  tfoot{display:table-footer-group}
  tr{break-inside:avoid;page-break-inside:avoid}
  td,th{break-inside:avoid;page-break-inside:avoid}
  h1,h2,h3,h4{break-after:avoid;page-break-after:avoid;break-inside:avoid}
  p,li,blockquote{orphans:3;widows:3}
  pre{break-inside:avoid;page-break-inside:avoid;white-space:pre-wrap}
  .letterhead{margin-bottom:22px}
  a{color:var(--ballpoint)}
  a[href^="http"]::after{
    content:" (" attr(href) ")";
    font-family:var(--mono);
    font-size:11px;
    color:var(--void);
    word-break:break-all;
  }
}
""".strip()


def _design_css(look) -> str:
    """The studio's choices, as a stylesheet that wins over the base one.

    Written as overrides rather than as a template of the whole sheet: the base
    stylesheet stays the single description of how a PRISM document is built,
    and this says only what this studio does differently.
    """
    rules = [
        ":root{",
        f"  --ink:{look.brand_colour};",
        f"  --accent:{look.accent_colour};",
        # `--ballpoint` too, and its absence here was a real bug rather than a
        # style choice. The base sheet defines it as `#35655A` - pine, the
        # default accent from two palettes ago - and NOTHING overrode it, so
        # every link, the blockquote rule, the checked acceptance box and
        # `tr.is-total` (the grand total's rules AND its colour) printed pine
        # while the cover banner, the one rule reading `--accent`, printed the
        # studio's actual accent.
        #
        # It was invisible for as long as the default accent WAS pine: the two
        # agreed by accident. Changing the default to violet is what exposed
        # it, which is the shape of bug a per-file review cannot see - the
        # defect was in this file, the change that revealed it was in another.
        #
        # A document has one accent. Both names now resolve to it.
        f"  --ballpoint:{look.accent_colour};",
        f"  --display:{look.heading_stack};",
        f"  --body:{look.body_stack};",
        "}",
        f"@page{{margin:{look.margin_mm:g}mm}}",
        f".sheet{{padding:{look.margin_mm:g}mm}}",
        ".letterhead .logo{max-height:34px;width:auto;display:block}",
    ]

    if look.cover == "left":
        rules.append(".doc--cover h1,.doc--cover h3,.doc--cover>p{text-align:left}")
    elif look.cover == "banner":
        # The banner is the cover: it starts at the top of the page and carries
        # the offset itself, so the title does not sit twice as far down.
        rules.append(
            ".doc--cover h1{background:var(--accent);color:var(--paper);"
            "padding:34mm 12mm;margin:0 0 10mm;text-align:left}"
        )
        rules.append(".doc--cover>h1:first-of-type{margin-top:0}")
        rules.append("@media print{.doc--cover>h1:first-of-type{margin-top:0}}")
    else:
        rules.append(".doc--cover h1,.doc--cover h3{text-align:center}")

    if look.tables == "zebra":
        rules.append("table tbody tr:nth-child(even){background:var(--duplicate)}")
        rules.append("table td{border-bottom:0}")
    elif look.tables == "plain":
        rules.append("table td,table th{border:0}")
        rules.append("table thead th{border-bottom:1px solid var(--rule)}")

    # Page numbers are deliberately not touched here. This page has never
    # numbered itself: browsers print their own header and footer and no
    # stylesheet can switch those off, so a rule pretending to would be a
    # setting that changes nothing. The PDF, which owns its own furniture,
    # honours it - and the editor says which is which.

    return "\n".join(rules)


def render_print_html(
    markdown_text: str,
    title: str,
    estimate: Estimate,
    *,
    kind: str = "",
    brand: str = "",
    doc_label: str = "",
    reference_label: str = "Quotation",
    reference_text: str = "",
    cover_break: bool = False,
    design=None,
) -> str:
    """Wrap rendered markdown in a self-contained, printable A4 page.

    ``kind`` is optional (``"proposal"`` / ``"requirements"``). When it is not
    supplied the document class is inferred from ``title``; every output is
    complete and correct either way.

    ``brand``, ``doc_label`` and ``reference_label`` exist for the proposal,
    which is signed by a client and must not carry the name of the tool that
    produced it or be titled as a different document. Their defaults are exactly
    what the quotation has always printed.

    ``design`` is a ``ProposalDesign``. It changes only how the page looks -
    colour, type, margins, the logo, the ruling on tables. Nothing on it can
    reach the content, which is precisely why a studio is allowed to edit it.
    """
    duplicate = _is_duplicate(str(title or ""), str(kind or ""), markdown_text)

    body_html = markdown_to_html(markdown_text)
    if not body_html.strip():
        body_html = '<p class="empty">This document has no content yet.</p>'

    reference = ""
    project = ""
    client = ""
    currency = ""
    if estimate is not None:
        try:
            reference = reference_text or quotation_reference(estimate)
            project = str(getattr(estimate, "project_name", "") or "").strip()
            client = str(getattr(estimate, "client_name", "") or "").strip()
            currency = normalise_code(getattr(estimate, "currency", ""))
        except Exception:  # a malformed estimate must not cost us the document
            reference = project = client = currency = ""

    # Resolved before the letterhead is built: that is the first thing to read
    # it, and a design validated after use is not validated at all.
    look = resolve_design(design)
    logo = look.logo.strip()

    doc_label = doc_label.strip() or (
        "Duplicate · for the developer" if duplicate else "Original · for the client"
    )
    brand = brand.strip() or "PRISM"
    # A caller who supplies no title gets the document named after the kind of
    # work it belongs to. The short form: the letterhead below already prints
    # the project. Software keeps the words - lowercase 'r' and all - that every
    # quotation prepared before kinds existed has printed in this tab.
    requirements_title = (
        "Developer requirements"
        if kinds.is_software(estimate)
        else f"{kinds.noun_for(estimate)} Requirements"
    )
    page_title = str(title or "").strip() or (requirements_title if duplicate else "Quotation")

    # The studio's own footer line - a registration or a TIN - reads before the
    # document's own reference, because it is a statement about the studio
    # rather than about this page.
    colophon_bits = [
        bit for bit in (look.footer_note.strip(), reference, project, client, currency) if bit
    ]

    letterhead = [
        '<header class="letterhead">',
        (
            f'<img class="logo" src="{_escape(logo)}" alt="{_escape(brand)}">'
            if logo
            else f'<span class="brand">{_escape(brand)}</span>'
        ),
        f"<span class=\"doctype\">{_escape(doc_label)}</span>",
    ]
    if reference:
        letterhead.append(
            f'<span class="ref">{_escape(reference_label)} {_escape(reference)}</span>'
        )
    letterhead.append("</header>")

    colophon = ""
    if colophon_bits:
        colophon = (
            '<footer class="colophon">'
            + _escape(" · ".join(colophon_bits))
            + "</footer>"
        )

    body_class = "doc doc--duplicate" if duplicate else "doc doc--original"
    if cover_break:
        # The proposal's markdown ends its cover with a rule; on paper that rule
        # is where the page turns. On screen it stays a rule, because a screen
        # has no pages to turn.
        body_class += " doc--cover"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="PRISM">
<title>{_escape(page_title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONT_HREF}">
<style>
{_stylesheet()}
{_design_css(look)}
</style>
</head>
<body class="{body_class}">
<div class="sheet">
{"".join(letterhead)}
<main class="prose">
{body_html}
</main>
{colophon}
</div>
</body>
</html>
"""
