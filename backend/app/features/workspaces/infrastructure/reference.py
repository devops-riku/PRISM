"""The quotation number the client quotes back at you.

Two ways to make one, and the choice is the studio's:

  * **Incremental** - 0000001, 0000002, and so on. Reads as a business that has
    done this before, and a client asking about "ABC-0002001" is asking about
    one thing. It needs a counter that survives restarts, which is why the
    sequence is reserved atomically in SQL.
  * **Random** - a short base-36 draw. Says nothing about volume, which is
    sometimes the point.

Both are written in base 36 (0-9 then A-Z). That is not decoration: at seven
characters a decimal counter stops at 9,999,999 and then either widens - so old
references and new ones no longer sort or line up - or wraps and starts handing
out numbers that already belong to something. Base 36 gives the same seven
characters 78 billion values, so the overflow is theoretical rather than a thing
somebody has to handle later.

The reference is fixed at creation and stored on the estimate. It is never
recomputed, because a quotation whose number changes between the markdown and
the PDF is not a reference at all.
"""

from __future__ import annotations

import json
from secrets import randbelow

from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import database

__all__ = ["build", "next_sequence", "peek_sequence", "preview", "normalise_prefix", "WIDTH"]

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
WIDTH = 7
#: 36**7 - the point at which seven characters are exhausted.
CEILING = len(ALPHABET) ** WIDTH
COUNTER_FILENAME = "_reference.json"
DEFAULT_PREFIX = "Q"

#: The two things a studio numbers. They are separate series because they are
#: separate documents: a quotation may be revised twice and never become a
#: proposal, and a proposal is what a client signs. Sharing one counter would
#: make proposal 41 the forty-first *quotation*, which is a number nobody can
#: explain when it is read back over the phone.
QUOTATIONS = "quotations"
PROPOSALS = "proposals"
DEFAULT_PROPOSAL_PREFIX = "P"

def _path():
    """Former JSON location, retained only for idempotent migration."""
    return workspaces.root() / COUNTER_FILENAME


#: Where plain digits run out: 9,999,999 is the last seven-character decimal.
DECIMAL_CEILING = 10**WIDTH


def _encode(value: int) -> str:
    """`value` as seven characters: decimal while it fits, base 36 after that.

    Staying decimal for the first ten million matters more than it looks.
    "0002001" is a number a client can read back over the phone and an accounts
    department can file; "00001JL" is a code. Base 36 is the overflow, not the
    everyday case - it takes over only once seven decimal digits are exhausted,
    and then only to avoid the two bad alternatives: widening the reference so
    old ones no longer line up, or wrapping and reissuing numbers that already
    belong to a quotation somebody has.
    """
    number = max(0, int(value)) % CEILING
    if number < DECIMAL_CEILING:
        return str(number).rjust(WIDTH, "0")

    digits = ""
    while number:
        number, remainder = divmod(number, len(ALPHABET))
        digits = ALPHABET[remainder] + digits
    return digits.rjust(WIDTH, "0")


def normalise_prefix(value: str) -> str:
    """Letters only, upper case, at most four.

    Punctuation is dropped rather than rejected: the separator is this module's
    to add, and a studio typing "ABC-" means ABC.
    """
    letters = [char for char in (value or "").upper() if char.isalpha()]
    return "".join(letters[:4])


def _legacy_counters() -> dict:
    """Both series from the former JSON store.

    One file with a key per series. The file written before proposals had their
    own number holds `{"next": 41}` and nothing else, so that value is read as
    the quotation counter - the alternative is a studio's numbering restarting
    at 1 the day this shipped.
    """
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    counters = {key: value for key, value in data.items() if key in (QUOTATIONS, PROPOSALS)}
    if QUOTATIONS not in counters and "next" in data:
        counters[QUOTATIONS] = data["next"]
    return counters


def _import_legacy() -> None:
    if not database.legacy_scan_required():
        return
    counters = _legacy_counters()
    if not counters:
        return
    scope = workspaces.current()
    for series in (QUOTATIONS, PROPOSALS):
        if series not in counters:
            continue
        try:
            next_value = max(1, int(counters[series]))
        except (TypeError, ValueError):
            next_value = 1
        database.import_counter(scope, series, next_value)


def peek_sequence(series: str = QUOTATIONS) -> int:
    """The number the next incremental reference will use, without taking it."""
    _import_legacy()
    return database.peek_counter(workspaces.current(), series)


def next_sequence(series: str = QUOTATIONS) -> int:
    """Take the next number in one series and record the one after it.

    Reserved atomically by SQL before it is returned. Two tiers prepared from
    one brief must not receive the same reference, and neither must two people,
    processes, or API instances.
    """
    _import_legacy()
    return database.take_counter(workspaces.current(), series)


def build(
    prefix: str,
    mode: str,
    sequence: int | None = None,
    series: str = QUOTATIONS,
) -> str:
    """One reference, ready to print.

    `sequence` is only read in incremental mode, and is taken from that series'
    counter when it is not supplied.
    """
    head = normalise_prefix(prefix) or (
        DEFAULT_PROPOSAL_PREFIX if series == PROPOSALS else DEFAULT_PREFIX
    )
    if (mode or "").strip().lower() == "incremental":
        number = next_sequence(series) if sequence is None else sequence
        return f"{head}-{_encode(number)}"

    # Random: a draw from the same alphabet, so both modes look like they came
    # from the same system.
    return f"{head}-" + "".join(ALPHABET[randbelow(len(ALPHABET))] for _ in range(6))


def preview(prefix: str, mode: str, series: str = QUOTATIONS) -> str:
    """What the next reference will look like. Takes no number from the counter."""
    head = normalise_prefix(prefix) or (
        DEFAULT_PROPOSAL_PREFIX if series == PROPOSALS else DEFAULT_PREFIX
    )
    if (mode or "").strip().lower() == "incremental":
        return f"{head}-{_encode(peek_sequence(series))}"
    return f"{head}-{_encode(0)[:6].replace('0', 'X')}"
