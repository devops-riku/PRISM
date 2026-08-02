"""A real downloadable PDF, built from the same markdown the other renderers use.

There was no PDF here for a long time and the reason was deliberate: the browser
print dialog writes an excellent one from `html.py`, and every PDF library worth
having on Linux wants native dependencies that do not install cleanly on
Windows. That trade stops working the moment somebody needs to attach a file to
an email rather than press Ctrl+P, which is what this module is for.

`reportlab` is pure Python, so it installs from a wheel on every platform this
runs on. It draws rather than renders HTML, so the input here is the **markdown**
that `markdown.py` already produced - not the `Estimate`. That matters: a second
renderer reading the estimate directly would be a second opinion about what the
document says, and the two would drift. There is one source of content and this
file only translates it into flowables.

Typography is the one place this cannot simply follow docs/DESIGN.md. The app is
set in Figtree, which reportlab cannot draw without a TTF in the repository, and
a webfont is not worth a megabyte and a licence to track. So the faces come from
the machine: `pdffonts` finds an installed family that can print money, and the
studio's design can ask for a different one - see `_sheet`. Colour, spacing and
rules all follow the kit, with the brand and accent colours overriding the kit's
two where a studio has set them.
"""

from __future__ import annotations

import io
import re
from functools import lru_cache
from typing import List, NamedTuple, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from app.design import font_for, resolve as resolve_design
from app.renderers.pdffonts import chosen as resolve_chosen, resolve as resolve_fonts
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app import kinds
from app.schemas import Estimate

from .markdown import quotation_reference

__all__ = ["render_pdf"]


# --- the Clarity Kit palette, as reportlab colours ---------------------------

INK = colors.HexColor("#1D1B17")
BODY = colors.HexColor("#4A443B")
VOID = colors.HexColor("#6B6459")
FAINT = colors.HexColor("#8A8378")
RULE = colors.HexColor("#E4DED4")
HAIRLINE = colors.HexColor("#EFEAE2")
ACCENT = colors.HexColor("#35655A")
ACCENT_SOFT = colors.HexColor("#EDF1EF")
PAPER = colors.HexColor("#FFFDFA")
RAISE = colors.HexColor("#F1EDE5")

#: Resolved once, at import. A font that carries the peso sign if the machine
#: has one, and Helvetica if it does not - see pdffonts.py for why this is not a
#: constant any more.
FONTS = resolve_fonts()

#: Symbols WinAnsi cannot draw. reportlab does not fail on a missing glyph, it
#: draws a black box - so when the fonts cannot carry these, the renderer prints
#: the ISO code instead. A client reading "PHP 1,500,000.00" is reading a
#: correct document; one reading a box is reading a broken one.
_UNPRINTABLE_SYMBOLS = {
    "\u20b1": "PHP ",
    "\u20b9": "INR ",
    "\u20ab": "VND ",
    "\u20a9": "KRW ",
    "\u0e3f": "THB ",
    "\u20ba": "TRY ",
    "\u20bd": "RUB ",
    "\u20b4": "UAH ",
    "\u0631.\u0625": "AED ",
}


def _printable(text: str, money: bool) -> str:
    """Swap any symbol the chosen font cannot draw for its ISO code."""
    if money:
        return text
    for symbol, code in _UNPRINTABLE_SYMBOLS.items():
        if symbol in text:
            text = text.replace(symbol, code)
    return text

PAGE_MARGIN = 18 * mm


class Sheet(NamedTuple):
    """One render's typographic settings, resolved from the studio's design.

    Built per document rather than at import, because two studios can be asking
    for two different looks in the same process. Cached, because resolving a
    family means touching the filesystem and registering fonts.
    """

    styles: dict
    #: The body faces, kept for measuring column widths in the same font the
    #: cells will actually be set in.
    fonts: object
    ink: colors.Color
    accent: colors.Color
    #: False when either face lacks the currency symbols, so money prints as
    #: ISO codes rather than as black boxes.
    money: bool


def _style(name: str, regular: str, **kwargs) -> ParagraphStyle:
    base = dict(
        name=name,
        fontName=regular,
        fontSize=9.5,
        leading=14,
        textColor=BODY,
        spaceBefore=0,
        spaceAfter=6,
        alignment=TA_LEFT,
    )
    base.update(kwargs)
    return ParagraphStyle(**base)


def _stylesheet(heading, body, ink: colors.Color) -> dict:
    """The document's styles, in the studio's faces and heading colour.

    Headings take the display face and the brand colour; everything that is read
    rather than scanned stays in the body face at the kit's own reading greys. A
    studio choosing a brand colour is choosing what its name and its headings
    look like, not making the body copy harder to read.
    """
    return {
        "title": _style("title", heading.bold, fontSize=22, leading=26, textColor=ink, spaceAfter=14),
        "h2": _style("h2", heading.bold, fontSize=14, leading=18, textColor=ink, spaceBefore=16, spaceAfter=8),
        "h3": _style("h3", heading.bold, fontSize=11, leading=15, textColor=ink, spaceBefore=12, spaceAfter=5),
        "h4": _style("h4", heading.bold, fontSize=9, leading=13, textColor=VOID, spaceBefore=10, spaceAfter=4),
        "body": _style("body", body.regular),
        "bullet": _style("bullet", body.regular, leftIndent=12, bulletIndent=2, spaceAfter=3),
        "task": _style("task", body.regular, spaceAfter=0),
        "cell": _style("cell", body.regular, fontSize=8.5, leading=12, spaceAfter=0),
        "cell_right": _style("cell_right", body.regular, fontSize=8.5, leading=12, spaceAfter=0, alignment=TA_RIGHT),
        "head": _style("head", heading.bold, fontSize=7.5, leading=11, textColor=FAINT, spaceAfter=0),
        "head_right": _style(
            "head_right", heading.bold, fontSize=7.5, leading=11, textColor=FAINT, spaceAfter=0, alignment=TA_RIGHT
        ),
        "quote": _style("quote", body.regular, leftIndent=10, textColor=VOID),
        "footer": _style("footer", body.regular, fontSize=7.5, leading=10, textColor=FAINT),
    }


@lru_cache(maxsize=32)
def _sheet_for(heading_key: str, body_key: str, ink_hex: str, accent_hex: str) -> Sheet:
    heading = resolve_chosen(font_for(heading_key).pdf_files, FONTS)
    body = resolve_chosen(font_for(body_key).pdf_files, FONTS)
    return Sheet(
        styles=_stylesheet(heading, body, colors.HexColor(ink_hex)),
        fonts=body,
        ink=colors.HexColor(ink_hex),
        accent=colors.HexColor(accent_hex),
        money=heading.unicode_money and body.unicode_money,
    )


def _sheet(look) -> Sheet:
    """The sheet for a resolved design."""
    return _sheet_for(look.heading_font, look.body_font, look.brand_colour, look.accent_colour)


class CheckBox(Flowable):
    """The acceptance-criterion box, drawn rather than typed.

    There is no font route to a hollow square here. Helvetica is WinAnsi and has
    no box glyph at all, and reportlab collapses every ZapfDingbats bullet it
    does not recognise onto `n`, a *filled* square - which is worse than nothing,
    because a criterion nobody has met would render as one that is done.

    So it is vector: a rounded outline in the same weight and colour as the box
    on screen, filled with the accent and given a white tick when checked.
    """

    def __init__(self, size: float = 8.5, done: bool = False, accent: colors.Color = ACCENT) -> None:
        super().__init__()
        self.size = size
        self.done = done
        self.accent = accent
        self.width = size
        self.height = size

    def draw(self) -> None:
        canvas = self.canv
        size = self.size
        canvas.setLineWidth(0.9)
        canvas.setStrokeColor(self.accent if self.done else VOID)
        canvas.setFillColor(self.accent if self.done else PAPER)
        canvas.roundRect(0, 0, size, size, size * 0.18, stroke=1, fill=1)

        if self.done:
            canvas.setStrokeColor(PAPER)
            canvas.setLineWidth(1.1)
            canvas.setLineCap(1)
            path = canvas.beginPath()
            path.moveTo(size * 0.23, size * 0.52)
            path.lineTo(size * 0.42, size * 0.30)
            path.lineTo(size * 0.78, size * 0.72)
            canvas.drawPath(path)


def _task_table(items: Sequence[tuple[bool, str]], width: float, sheet: Sheet) -> Table:
    """Consecutive acceptance criteria as one box-and-text table."""
    rows = [
        [CheckBox(done=done, accent=sheet.accent), Paragraph(_inline(text, sheet.money), sheet.styles["task"])]
        for done, text in items
    ]
    table = Table(rows, colWidths=[16, width - 16], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (0, -1), 2.6),  # optically centre on line one
                ("TOPPADDING", (1, 0), (1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, -1), 6),
                ("RIGHTPADDING", (1, 0), (1, -1), 0),
            ]
        )
    )
    return table


# --- inline markdown ----------------------------------------------------------

_ESCAPES = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")
_CODE = re.compile(r"`([^`]+)`")
_STRONG = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_EM = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*(?![\w*])")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")


def _inline(text: str, money: bool = True) -> str:
    """Markdown to the small tag subset reportlab's Paragraph understands.

    Every string in the document passes through here, which makes it the one
    place to swap a symbol the font cannot draw. Doing it per-caller would mean
    finding every caller, and the one missed would print a black box. `money`
    is the chosen face's verdict on whether it can draw a currency symbol at
    all, which is why it travels with the sheet rather than being a constant.
    """
    out = (
        _printable(str(text or ""), money)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    out = _CODE.sub(lambda m: f'<font face="Courier">{m.group(1)}</font>', out)
    out = _LINK.sub(lambda m: m.group(1) or m.group(2), out)
    out = _STRONG.sub(r"<b>\1</b>", out)
    out = _EM.sub(r"<i>\1</i>", out)
    out = _ESCAPES.sub(r"\1", out)
    return out


def _plain(text: str) -> str:
    return re.sub(r"[*`~]|\\(?=.)", "", str(text or "")).strip()


# --- block markdown -----------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_HR = re.compile(r"^\s*(?:-{3,}|_{3,}|\*{3,})\s*$")
_DIVIDER = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")

_NUMERIC = re.compile(r"^[^A-Za-z]*\d[\d.,\s%×/+-]*$")


def _split_row(line: str) -> List[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT.split(text)]


def _table_flowable(rows: List[List[str]], width: float, sheet: Sheet) -> Table:
    """A markdown table as a ruled reportlab Table.

    Column widths are proportional to the longest cell in each column, with the
    first column given the slack, because in every table these documents produce
    it is the one holding prose while the rest hold figures.
    """
    header, *body = rows
    columns = max(len(row) for row in rows)
    header = header + [""] * (columns - len(header))

    # Right-align a column when its body cells are figures.
    right = []
    for index in range(columns):
        values = [row[index] for row in body if index < len(row) and _plain(row[index])]
        right.append(bool(values) and all(_NUMERIC.match(_plain(v)) for v in values))

    longest = []
    for index in range(columns):
        cells = [header[index]] + [row[index] for row in body if index < len(row)]
        longest.append(max((len(_plain(c)) for c in cells), default=1))
    total = sum(longest) or 1
    widths = [max(width * 0.07, width * (value / total)) for value in longest]

    # A figures column gets whatever its widest number actually measures, not a
    # share of the table. Proportional widths split "1,500,000.00" across two
    # lines as "1,500,000.0" and "0", which reads as a different number - and
    # unlike prose, a number has nowhere sensible to break.
    floors = [0.0] * columns
    for index in range(columns):
        if not right[index]:
            continue
        cells = [header[index]] + [row[index] for row in body if index < len(row)]
        measured = max(
            (
                stringWidth(_printable(_plain(cell), sheet.money), sheet.fonts.bold, 9.5)
                for cell in cells
                if _plain(cell)
            ),
            default=0.0,
        )
        floors[index] = min(width * 0.34, measured + 14)

    widths = [max(floor, value) for floor, value in zip(floors, widths)]

    # Give the overflow back to the prose columns, which can wrap, rather than
    # scaling every column down again and undoing the floors.
    overflow = sum(widths) - width
    if overflow > 0:
        flexible = [i for i in range(columns) if not right[i] and widths[i] > width * 0.09]
        if flexible:
            share = overflow / len(flexible)
            for index in flexible:
                widths[index] = max(width * 0.09, widths[index] - share)
    scale = width / sum(widths)
    widths = [value * scale for value in widths]

    data = [
        [Paragraph(_inline(cell, sheet.money), sheet.styles["head_right"] if right[i] else sheet.styles["head"])
         for i, cell in enumerate(header)]
    ]
    for row in body:
        padded = row + [""] * (columns - len(row))
        data.append(
            [Paragraph(_inline(cell, sheet.money), sheet.styles["cell_right"] if right[i] else sheet.styles["cell"])
             for i, cell in enumerate(padded[:columns])]
        )

    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, sheet.ink),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, HAIRLINE),
    ]

    # A bold first cell in the last rows is a total; rule above it.
    for index, row in enumerate(body, start=1):
        if row and row[0].strip().startswith("**") and "total" in _plain(row[0]).lower():
            style.append(("LINEABOVE", (0, index), (-1, index), 0.9, sheet.ink))
            style.append(("TEXTCOLOR", (0, index), (-1, index), sheet.ink))

    table.setStyle(TableStyle(style))
    return table


def _flowables(markdown_text: str, width: float, sheet: Sheet, *, cover_break: bool = False) -> list:
    """Translate a whole document into flowables.

    `cover_break` turns the FIRST horizontal rule into a page break. The
    proposal's markdown ends its cover with one, and this is where that means
    "new page" rather than "a little air". It also drops the title down the
    page: a cover exists to say what the document is and who it is for, and a
    title tight under the letterhead reads as page one of a report instead.
    """
    lines = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    flow: list = []
    index = 0

    if cover_break:
        flow.append(Spacer(1, 58 * mm))

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        # Table: a pipe row followed by a divider row.
        if stripped.startswith("|") and index + 1 < len(lines) and _DIVIDER.match(lines[index + 1]):
            rows = [_split_row(stripped)]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_split_row(lines[index]))
                index += 1
            # Drop spacer rows, which exist only to breathe in a text editor.
            rows = [row for row in rows if any(_plain(cell) for cell in row)]
            if len(rows) > 1:
                flow.append(Spacer(1, 4))
                flow.append(_table_flowable(rows, width, sheet))
                flow.append(Spacer(1, 10))
            continue

        heading = _HEADING.match(stripped)
        if heading:
            level = len(heading.group(1))
            text = _inline(heading.group(2), sheet.money)
            key = {1: "title", 2: "h2", 3: "h3"}.get(level, "h4")
            flow.append(Paragraph(text, sheet.styles[key]))
            index += 1
            continue

        if stripped == "&nbsp;":
            flow.append(Spacer(1, 22))
            index += 1
            continue

        if _HR.match(stripped):
            if cover_break:
                flow.append(PageBreak())
                cover_break = False
            else:
                flow.append(Spacer(1, 8))
            index += 1
            continue

        bullet = _BULLET.match(stripped)
        if bullet:
            task = _TASK.match(bullet.group(1))
            if task:
                # Gather the whole checklist so the boxes align as one block.
                criteria = []
                while index < len(lines):
                    nxt = _BULLET.match(lines[index].strip())
                    marked = _TASK.match(nxt.group(1)) if nxt else None
                    if not marked:
                        break
                    criteria.append((marked.group(1).lower() == "x", marked.group(2)))
                    index += 1
                flow.append(_task_table(criteria, width, sheet))
                flow.append(Spacer(1, 3))
            else:
                flow.append(Paragraph(_inline(bullet.group(1), sheet.money), sheet.styles["bullet"], bulletText="•"))
                index += 1
            continue

        ordered = _ORDERED.match(stripped)
        if ordered:
            flow.append(
                Paragraph(_inline(ordered.group(2), sheet.money), sheet.styles["bullet"], bulletText=f"{ordered.group(1)}.")
            )
            index += 1
            continue

        if stripped.startswith(">"):
            flow.append(Paragraph(_inline(stripped.lstrip("> "), sheet.money), sheet.styles["quote"]))
            index += 1
            continue

        # A paragraph runs until a blank line or the next block.
        paragraph = [stripped]
        index += 1
        while index < len(lines):
            nxt = lines[index].strip()
            if not nxt or nxt.startswith(("|", "#", ">")) or _BULLET.match(nxt) or _ORDERED.match(nxt) or _HR.match(nxt):
                break
            paragraph.append(nxt)
            index += 1
        flow.append(Paragraph(_inline(" ".join(paragraph), sheet.money), sheet.styles["body"]))

    return flow


# --- page furniture -----------------------------------------------------------


def _page_furniture(
    estimate: Estimate,
    label: str,
    accent_edge: bool,
    look=None,
    margin: float | None = None,
):
    """Draw the running header rule, the accent edge and the page number.

    `look` carries the studio's colours, logo and footer note; `margin` is the
    page margin in points, which the design can widen or tighten.
    """
    reference = quotation_reference(estimate)
    subject = (estimate.client_name or estimate.project_name or "").strip()
    look = resolve_design(look)
    sheet = _sheet(look)
    edge = colors.HexColor(look.accent_colour)
    margin = PAGE_MARGIN if margin is None else margin
    logo = look.logo_bytes()

    def draw(canvas, doc):
        canvas.saveState()
        width, height = A4

        if accent_edge:
            canvas.setFillColor(edge)
            canvas.rect(0, 0, 3.5 * mm, height, stroke=0, fill=1)

        canvas.setFont(sheet.fonts.regular, 7.5)
        canvas.setFillColor(FAINT)
        top = height - margin + 6 * mm

        # The studio's mark, where there is one, stands where the label would.
        # It says the same thing better, and it is the first thing a client
        # recognises on a page they are being asked to sign.
        drawn = False
        if logo:
            try:
                image = ImageReader(io.BytesIO(logo))
                iw, ih = image.getSize()
                if iw and ih:
                    tall = 7 * mm
                    canvas.drawImage(
                        image,
                        margin,
                        top - 2 * mm,
                        width=tall * (iw / ih),
                        height=tall,
                        mask="auto",
                        preserveAspectRatio=True,
                    )
                    drawn = True
            except Exception:  # a logo that will not decode must not cost the page
                drawn = False
        if not drawn:
            canvas.drawString(margin, top, label.upper())

        right = " · ".join(part for part in (subject, reference) if part)
        canvas.drawRightString(width - margin, top, right)

        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(margin, top - 2.5 * mm, width - margin, top - 2.5 * mm)

        foot = look.footer_note.strip()
        if foot:
            canvas.drawString(margin, margin - 8 * mm, foot)
        if look.page_numbers:
            canvas.drawCentredString(width / 2, margin - 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def render_pdf(
    markdown_text: str,
    title: str,
    estimate: Estimate,
    *,
    kind: str = "",
    doc_label: str = "",
    cover_break: bool = False,
    design=None,
) -> bytes:
    """Render one document to PDF bytes.

    `kind` is "proposal" or "requirements"; the developer document gets the
    accent edge down the page, the same signal its HTML and on-screen versions
    carry, so the two are distinguishable in a printed stack.

    `doc_label` overrides the words stamped on every page. It exists because a
    proposal is signed by a client, and a page footer reading "Quotation" on the
    document they are signing is simply wrong.

    `cover_break` turns the document's first horizontal rule into a page break,
    which is how the proposal gets a cover page: the markdown says where the
    cover ends, and each renderer decides what that means in its own medium.

    `design` is a `ProposalDesign`. Colour, typeface, margins, the logo and the
    footer note are all honoured. Cover layout and table ruling are not: those
    are structural in reportlab rather than a stylesheet away, and the editor
    says so rather than quietly producing a PDF that does not match the print
    view a studio approved.
    """
    is_developer = (kind or "").strip().lower().startswith("requirement")
    if not kind:
        is_developer = "requirement" in (title or "").lower()

    buffer = io.BytesIO()
    # The design decides how much air the page has. Everything else about the
    # layout is the kit's; a studio choosing "roomy" is choosing a margin, not
    # redesigning a document.
    look = resolve_design(design)
    margin = look.margin_mm * mm
    sheet = _sheet(look)

    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin + 4 * mm,
        bottomMargin=margin,
        title=title or "Quotation",
        author=(estimate.client_name or "PRISM").strip() or "PRISM",
        subject=(estimate.project_name or "").strip(),
    )

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        id="body",
    )
    # Stamped at the top of every page, so it stays the short form: the furniture
    # already prints the client and the reference on the right, and the kind's
    # noun is what changes. Software keeps the words it has always stamped.
    requirements_label = (
        "Developer requirements"
        if kinds.is_software(estimate)
        else f"{kinds.noun_for(estimate)} Requirements"
    )
    label = doc_label.strip() or (requirements_label if is_developer else "Quotation")
    doc.addPageTemplates(
        [
            PageTemplate(
                id="page",
                frames=[frame],
                onPage=_page_furniture(estimate, label, is_developer, look, margin),
            )
        ]
    )

    story = _flowables(markdown_text, doc.width, sheet, cover_break=cover_break)
    if not story:
        story = [Paragraph("This document is empty.", sheet.styles["body"])]

    doc.build(story)
    return buffer.getvalue()
