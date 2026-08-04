"""How a proposal looks, as data.

The content of a proposal is generated - figures printed from the quotation,
terms verbatim from the studio's clauses, prose written per document. None of
that is editable by hand, and it must not become editable, because the one
guarantee this system makes is that the numbers in a document cannot disagree
with the quotation behind them.

What a studio genuinely needs to control is the *look*: their logo, their
colours, their typeface, how the cover is laid out, how tight the margins are.
None of that can contradict a figure. So the design is a small, closed set of
choices, and both renderers read it - the printable HTML and the PDF - so a
proposal looks the same wherever it is opened.

Every value is validated here. A colour that is not a colour, a margin of four
metres or a logo of eleven megabytes reaches the renderers as the default
instead of as a broken document.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import NamedTuple

from pydantic import BaseModel, Field

__all__ = ["ProposalDesign", "FontChoice", "resolve", "PALETTE", "FONTS", "COVERS", "TABLES"]

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

#: A logo is embedded in every export, so it is capped. 400 KB of base64 is a
#: generous PNG and a small fraction of the PDF it lands in.
MAX_LOGO_BYTES = 400_000
_DATA_URI = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=\s]+)$")


class FontChoice(NamedTuple):
    """One typeface, named for each renderer that has to draw it."""

    key: str
    label: str
    #: CSS stack for the printable page.
    css: str
    #: Candidate families for the PDF, in preference order, each written the way
    #: pdffonts wants them: (family, regular, bold, italic). Empty means "use
    #: whatever pdffonts resolved at import", which is the app's own default.
    pdf_files: tuple


#: A short list on purpose. Every entry has to exist in both worlds - a webfont
#: the PDF cannot draw would give a studio a design that changes shape when they
#: export it, which is worse than a shorter menu.
FONTS: tuple = (
    FontChoice(
        "sans",
        "Figtree (the app's own)",
        '"Figtree", ui-sans-serif, system-ui, sans-serif',
        (),
    ),
    FontChoice(
        "grotesque",
        "Segoe UI / Arial",
        '"Segoe UI", Arial, Helvetica, sans-serif',
        (
            ("Segoe UI", "segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"),
            ("Arial", "arial.ttf", "arialbd.ttf", "ariali.ttf"),
            (
                "Liberation Sans",
                "LiberationSans-Regular.ttf",
                "LiberationSans-Bold.ttf",
                "LiberationSans-Italic.ttf",
            ),
        ),
    ),
    FontChoice(
        "serif",
        "Times / Georgia",
        'Georgia, "Times New Roman", Times, serif',
        (
            ("Times New Roman", "times.ttf", "timesbd.ttf", "timesi.ttf"),
            ("Georgia", "georgia.ttf", "georgiab.ttf", "georgiai.ttf"),
            (
                "Liberation Serif",
                "LiberationSerif-Regular.ttf",
                "LiberationSerif-Bold.ttf",
                "LiberationSerif-Italic.ttf",
            ),
            ("DejaVu Serif", "DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf"),
        ),
    ),
)

FONT_KEYS = {font.key for font in FONTS}

#: Cover layouts, named for what they do rather than for a CSS property.
COVERS = ("centred", "left", "banner")

#: How the tables are ruled.
TABLES = ("ruled", "zebra", "plain")

#: Page margins, in millimetres.
MARGINS = {"compact": 14.0, "standard": 18.0, "roomy": 24.0}

#: The document's own two colours, and they are NOT the app's.
#:
#: A quotation is printed and emailed; the app is looked at on a screen. So the
#: brand is the indigo the app uses for its own ink, which is legible on paper
#: at 12.51:1 - and the accent is a DEEPER violet than the app's `#8b7cf6`,
#: because it does a different job here. In the app that violet is type on a
#: dark ground. Here it is a filled banner with paper-coloured text ON it, and
#: white on `#8b7cf6` measures 3.33 - under AA for body text. `#6d57e8` is the
#: same violet family at 5.03, which clears it with headroom.
#:
#: Mirrored by hand in `frontend/src/components/DesignEditor.tsx`, which cannot
#: import from Python. Change one and change the other.
PALETTE = {"brand": "#343148", "accent": "#6D57E8"}


class ProposalDesign(BaseModel):
    """The look of a proposal. Content is not configurable here, only its dress."""

    logo: str = Field(
        default="",
        description=(
            "A data: URI for the studio's mark, embedded in every export. Empty prints the "
            "studio name as text instead."
        ),
    )
    brand_colour: str = Field(default=PALETTE["brand"], description="Headings and rules.")
    accent_colour: str = Field(default=PALETTE["accent"], description="Totals, links, the edge.")
    heading_font: str = Field(default="sans", description="One of FONTS.")
    body_font: str = Field(default="sans", description="One of FONTS.")
    cover: str = Field(default="centred", description="centred | left | banner.")
    margins: str = Field(default="standard", description="compact | standard | roomy.")
    tables: str = Field(default="ruled", description="ruled | zebra | plain.")
    page_numbers: bool = Field(default=True, description="Print a page number in the footer.")
    footer_note: str = Field(
        default="",
        description="One line in the footer, e.g. a registration number. Empty prints nothing.",
    )

    # --- derived, for the renderers ---------------------------------------

    @property
    def heading_stack(self) -> str:
        return _font(self.heading_font).css

    @property
    def body_stack(self) -> str:
        return _font(self.body_font).css

    @property
    def margin_mm(self) -> float:
        return MARGINS.get(self.margins, MARGINS["standard"])

    def logo_bytes(self) -> bytes:
        """The decoded logo, or b"" when there is not a usable one."""
        match = _DATA_URI.match((self.logo or "").strip())
        if not match:
            return b""
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
        except (binascii.Error, ValueError):
            return b""
        return raw if len(raw) <= MAX_LOGO_BYTES else b""


def _font(key: str) -> FontChoice:
    for font in FONTS:
        if font.key == key:
            return font
    return FONTS[0]


def font_for(key: str) -> FontChoice:
    """The typeface for a stored key, falling back to the app's own."""
    return _font(key)


def _colour(value: str, fallback: str) -> str:
    text = (value or "").strip()
    return text if _HEX.match(text) else fallback


def resolve(design: ProposalDesign | None) -> ProposalDesign:
    """A design safe to hand to a renderer.

    Every field is checked rather than trusted: a settings file can be edited by
    hand, and a malformed colour would otherwise reach the stylesheet as a
    literal and take the whole document's styling with it.
    """
    source = design or ProposalDesign()
    logo = (source.logo or "").strip()
    if logo and not ProposalDesign(logo=logo).logo_bytes():
        logo = ""

    return ProposalDesign(
        logo=logo,
        brand_colour=_colour(source.brand_colour, PALETTE["brand"]),
        accent_colour=_colour(source.accent_colour, PALETTE["accent"]),
        heading_font=source.heading_font if source.heading_font in FONT_KEYS else "sans",
        body_font=source.body_font if source.body_font in FONT_KEYS else "sans",
        cover=source.cover if source.cover in COVERS else "centred",
        margins=source.margins if source.margins in MARGINS else "standard",
        tables=source.tables if source.tables in TABLES else "ruled",
        page_numbers=bool(source.page_numbers),
        footer_note=(source.footer_note or "").strip()[:120],
    )
