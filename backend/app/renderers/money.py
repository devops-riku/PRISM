"""Money, quantity and percentage formatting for PRISM documents.

There is no FX conversion anywhere in PRISM: line items are already priced in the
selected currency. These helpers only decide how a number is *printed*.

Rules encoded here:

* Currencies with a well-known symbol print the symbol immediately before the
  digits (``PHP 1234.5 -> "P1,234.50"`` with the peso sign). Everything else
  falls back to ``"CODE 1,234.56"`` - a form that is unambiguous in every market
  and never invents a placement we cannot verify.
* ISO 4217 zero-decimal currencies (JPY, KRW, VND and the rest of the official
  list) print without a fractional part.
* Rounding is half-up on the decimal value, not half-even on the binary float,
  because a quotation that prints 1,234.56 must not print 1,234.55 elsewhere.
* A negative amount prints with a leading ASCII hyphen, before the symbol.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from typing import Dict, FrozenSet, Iterable, Tuple

__all__ = [
    "CURRENCY_SYMBOLS",
    "ZERO_DECIMAL_CURRENCIES",
    "UNIT_LABELS",
    "normalise_code",
    "currency_symbol",
    "currency_decimals",
    "format_money",
    "format_qty",
    "format_pct",
    "format_unit",
    "format_quantity_with_unit",
    "sum_money",
]


# --- currency tables ---------------------------------------------------------

#: Symbol placed immediately before the digits. Codes absent from this table use
#: the ``"CODE 1,234.56"`` fallback on purpose - CHF, SEK, PLN, AED and friends
#: read better that way than with an invented glyph.
CURRENCY_SYMBOLS: Dict[str, str] = {
    "PHP": "₱",   # peso sign
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "AUD": "A$",
    "CAD": "C$",
    "NZD": "NZ$",
    "SGD": "S$",
    "HKD": "HK$",
    "TWD": "NT$",
    "CNY": "CN¥",
    "KRW": "₩",
    "VND": "₫",
    "THB": "฿",
    "INR": "₹",
    "IDR": "Rp",
    "MYR": "RM",
    "BRL": "R$",
    "MXN": "MX$",
    "ZAR": "R",
    "NGN": "₦",
    "KES": "KSh",
    "TRY": "₺",
    "RUB": "₽",
    "ILS": "₪",
    "UAH": "₴",
    "BDT": "৳",
    "PKR": "Rs",
    "LKR": "Rs",
}

#: ISO 4217 currencies with zero minor units.
ZERO_DECIMAL_CURRENCIES: FrozenSet[str] = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG",
        "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)

#: Singular / plural display forms for :class:`app.schemas.UnitKind`.
UNIT_LABELS: Dict[str, Tuple[str, str]] = {
    "hour": ("hour", "hours"),
    "day": ("day", "days"),
    "week": ("week", "weeks"),
    "month": ("month", "months"),
    "item": ("item", "items"),
    "lump_sum": ("lump sum", "lump sums"),
}

_CODE_RE = re.compile(r"^[A-Z]{3}$")
_NON_ALPHA = re.compile(r"[^A-Za-z]+")


# --- primitives --------------------------------------------------------------


def _to_decimal(value: object) -> Decimal:
    """Best-effort conversion to a finite Decimal. Never raises."""
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, bool):  # bool is an int; treat as 0/1 explicitly
        candidate = Decimal(int(value))
    elif isinstance(value, int):
        candidate = Decimal(value)
    else:
        try:
            as_float = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return Decimal(0)
        if not isfinite(as_float):
            return Decimal(0)
        try:
            candidate = Decimal(repr(as_float))
        except InvalidOperation:
            return Decimal(0)
    if not candidate.is_finite():
        return Decimal(0)
    return candidate


def _quantize(value: object, decimals: int) -> Decimal:
    exponent = Decimal(1).scaleb(-decimals)
    try:
        return _to_decimal(value).quantize(exponent, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal(0).quantize(exponent)


def normalise_code(currency: object) -> str:
    """Return a clean upper-case currency code, or "" when there is none."""
    text = "" if currency is None else str(currency).strip().upper()
    if not text:
        return ""
    if _CODE_RE.match(text):
        return text
    letters = _NON_ALPHA.sub("", text).upper()
    return letters[:3] if len(letters) >= 3 else letters


def currency_symbol(currency: object) -> str:
    """The symbol for a currency, or "" when we print the code instead."""
    return CURRENCY_SYMBOLS.get(normalise_code(currency), "")


def currency_decimals(currency: object) -> int:
    """Number of fractional digits this currency is written with."""
    return 0 if normalise_code(currency) in ZERO_DECIMAL_CURRENCIES else 2


# --- public formatters -------------------------------------------------------


def format_money(amount: float, currency: str = "PHP") -> str:
    """Format ``amount`` in ``currency``.

    >>> format_money(1234.5, "PHP")
    '₱1,234.50'
    >>> format_money(1234.5, "JPY")
    '¥1,235'
    >>> format_money(1234.56, "CHF")
    'CHF 1,234.56'
    >>> format_money(-5000, "USD")
    '-$5,000.00'
    """
    code = normalise_code(currency)
    decimals = currency_decimals(code)
    value = _quantize(amount, decimals)

    negative = value < 0
    if negative:
        value = -value

    digits = f"{value:,.{decimals}f}"
    symbol = CURRENCY_SYMBOLS.get(code, "")

    if symbol:
        body = f"{symbol}{digits}"
    elif code:
        body = f"{code} {digits}"
    else:
        body = digits

    return f"-{body}" if negative else body


def format_amount(amount: float, currency: str = "PHP") -> str:
    """The same figure without its symbol, for a column that states it once.

    A table where every cell carries the peso sign is a table where the sign is
    noise: it repeats twenty times and adds nothing after the first. Accounting
    practice is to name the currency in the column heading and print bare
    figures under it, keeping the symbol for the totals a reader stops at.

    Decimals still follow the currency, so a yen column has none and a peso
    column has two - the symbol is what goes, not the arithmetic.

    >>> format_amount(1234.5, "PHP")
    '1,234.50'
    >>> format_amount(1234.5, "JPY")
    '1,235'
    >>> format_amount(-5000, "USD")
    '-5,000.00'
    """
    decimals = currency_decimals(normalise_code(currency))
    value = _quantize(amount, decimals)
    negative = value < 0
    if negative:
        value = -value
    digits = f"{value:,.{decimals}f}"
    return f"-{digits}" if negative else digits


def format_qty(value: float, max_decimals: int = 2) -> str:
    """Format a quantity: thousands separated, trailing zeros dropped.

    >>> format_qty(40.0), format_qty(2.5), format_qty(1200)
    ('40', '2.5', '1,200')
    """
    decimals = max(0, min(int(max_decimals), 6))
    text = f"{_quantize(value, decimals):,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_pct(value: float, max_decimals: int = 2) -> str:
    """Format a percentage stored as a number of percent (``10`` -> ``"10%"``)."""
    return f"{format_qty(value, max_decimals)}%"


def format_unit(unit: object, quantity: float = 1.0) -> str:
    """Human label for a :class:`app.schemas.UnitKind`, pluralised by quantity.

    >>> format_unit("lump_sum"), format_unit("hour", 8)
    ('lump sum', 'hours')
    """
    raw = getattr(unit, "value", unit)
    key = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not key:
        return ""

    singular, plural = UNIT_LABELS.get(key, ("", ""))
    if not singular:
        singular = key.replace("_", " ")
        plural = singular if singular.endswith("s") else f"{singular}s"

    return singular if abs(_to_decimal(quantity)) == 1 else plural


def format_quantity_with_unit(quantity: float, unit: object) -> str:
    """``8, UnitKind.hour`` -> ``"8 hours"``. Empty string when both are blank."""
    label = format_unit(unit, quantity)
    number = format_qty(quantity)
    if not label:
        return number
    return f"{number} {label}"


def sum_money(values: Iterable[float], currency: str = "PHP") -> str:
    """Format the sum of already-priced values. Used for category subtotals."""
    decimals = currency_decimals(currency)
    total = Decimal(0)
    for value in values:
        total += _quantize(value, decimals)
    return format_money(total, currency)
