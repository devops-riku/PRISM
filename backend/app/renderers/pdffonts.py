"""Finding a font the PDF can actually print money in.

reportlab's built-in Helvetica is WinAnsi. It has no peso sign, no rupee, no
dong - and reportlab does not fail on a missing glyph, it draws a black box. So
a Philippine quotation exported to PDF read "■1,500,000.00", which is worse than
wrong: it is a document that looks broken to the client who receives it.

Shipping a TTF in the repository would fix it and cost a megabyte plus a licence
to track. Instead this looks for a font already on the machine that carries the
symbol, registers it, and says so. When nothing suitable is found the renderer
is told, and it prints the ISO code - "PHP 1,500,000.00" - which is plain, is
correct, and is what a bank statement does anyway.

Nothing here raises. A PDF that renders in Helvetica with ISO codes is a working
document; an exception on import is not.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, NamedTuple

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger("prism.pdf")

__all__ = ["FontSet", "resolve", "chosen"]

#: The symbol that started this. Any font chosen must carry it, because the
#: home market is the Philippines; the others are checked so a studio quoting in
#: rupees or dong gets the same treatment.
REQUIRED = 0x20B1  # ₱
PREFERRED = (0x20B9, 0x20AB, 0x20A9, 0x20AC, 0x00A3)  # ₹ ₫ ₩ € £

#: Families that ship with the platform, in the order they are worth having.
#: Each entry is (regular, bold, italic); a missing bold or italic simply falls
#: back to the regular face rather than disqualifying the family.
CANDIDATES = (
    ("Segoe UI", "segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"),
    ("Arial", "arial.ttf", "arialbd.ttf", "ariali.ttf"),
    ("Calibri", "calibri.ttf", "calibrib.ttf", "calibrii.ttf"),
    ("DejaVu Sans", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
    ("Liberation Sans", "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf"),
    ("Noto Sans", "NotoSans-Regular.ttf", "NotoSans-Bold.ttf", "NotoSans-Italic.ttf"),
)

SEARCH_PATHS = (
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/noto"),
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
)


class FontSet(NamedTuple):
    """The faces the renderer should use, and whether symbols are safe."""

    regular: str
    bold: str
    italic: str
    #: False means the fonts cannot draw a peso sign, so print ISO codes.
    unicode_money: bool
    source: str


FALLBACK = FontSet("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False, "built-in Helvetica")


def _find(filename: str) -> Path | None:
    for directory in SEARCH_PATHS:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def _carries_money(path: Path) -> bool:
    """True when this file has the peso sign in its cmap."""
    try:
        face = TTFont("prism-probe", str(path)).face
    except Exception:  # a font the platform has but reportlab cannot parse
        return False
    coverage = getattr(face, "charToGlyph", None) or {}
    return REQUIRED in coverage


def _register(name: str, path: Path) -> bool:
    try:
        pdfmetrics.registerFont(TTFont(name, str(path)))
        return True
    except Exception as exc:  # pragma: no cover - a corrupt system font
        logger.warning("Could not register %s from %s: %s", name, path, exc)
        return False


def _register_family(family: str, regular: str, bold_file: str, italic_file: str) -> FontSet | None:
    """Register one named family, whatever its currency coverage."""
    regular_path = _find(regular)
    if regular_path is None:
        return None

    base = f"PRISM-{family.replace(' ', '')}"
    if not _register(base, regular_path):
        return None

    bold = base
    bold_path = _find(bold_file)
    if bold_path is not None and _register(f"{base}-Bold", bold_path):
        bold = f"{base}-Bold"

    italic = base
    italic_path = _find(italic_file)
    if italic_path is not None and _register(f"{base}-Italic", italic_path):
        italic = f"{base}-Italic"

    pdfmetrics.registerFontFamily(base, normal=base, bold=bold, italic=italic, boldItalic=bold)
    return FontSet(
        base, bold, italic, _carries_money(regular_path), f"{family} ({regular_path})"
    )


def chosen(candidates: Iterable[tuple], fallback: FontSet) -> FontSet:
    """The typeface a studio picked in the design, as faces the PDF can draw.

    Two passes, in this order for a reason. A family that carries the peso sign
    is preferred, because symbols are worth more than a serif. But a studio that
    asked for a serif and has only Georgia installed - which has no peso glyph -
    gets Georgia and ISO currency codes rather than silently getting the sans
    they did not ask for: the codes are a documented, readable fallback, and a
    typeface that ignores the setting is not.

    Nothing here raises. An empty candidate list, a missing font or an
    unregisterable one all end at `fallback`, which is the app's own face.
    """
    families = tuple(candidates or ())
    if not families:
        return fallback

    for family, regular, bold, italic in families:
        path = _find(regular)
        if path is not None and _carries_money(path):
            found = _register_family(family, regular, bold, italic)
            if found is not None:
                return found

    for family, regular, bold, italic in families:
        found = _register_family(family, regular, bold, italic)
        if found is not None:
            logger.info(
                "PDF set in %s, which has no peso glyph; money prints as ISO codes.", family
            )
            return found

    return fallback


def resolve(candidates: Iterable[tuple] = CANDIDATES) -> FontSet:
    """The first installed family that can print money, or Helvetica.

    Called once at import. The result is cached by the module that imports it,
    not here, so a test can ask again with its own candidate list.
    """
    for family, regular_file, bold_file, italic_file in candidates:
        regular_path = _find(regular_file)
        if regular_path is None or not _carries_money(regular_path):
            continue

        base = f"PRISM-{family.replace(' ', '')}"
        if not _register(base, regular_path):
            continue

        bold = base
        bold_path = _find(bold_file)
        if bold_path is not None and _register(f"{base}-Bold", bold_path):
            bold = f"{base}-Bold"

        italic = base
        italic_path = _find(italic_file)
        if italic_path is not None and _register(f"{base}-Italic", italic_path):
            italic = f"{base}-Italic"

        pdfmetrics.registerFontFamily(base, normal=base, bold=bold, italic=italic, boldItalic=bold)

        missing = [hex(code) for code in PREFERRED if code not in TTFont(base, str(regular_path)).face.charToGlyph]
        logger.info(
            "PDF money set in %s from %s%s",
            family,
            regular_path,
            f" (no glyph for {', '.join(missing)})" if missing else "",
        )
        return FontSet(base, bold, italic, True, f"{family} ({regular_path})")

    logger.warning(
        "No installed font carries the peso sign; PDFs will print ISO currency codes instead "
        "of symbols. Install DejaVu Sans, Noto Sans or Liberation Sans to get symbols back."
    )
    return FALLBACK
