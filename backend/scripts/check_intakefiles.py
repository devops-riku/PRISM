"""A client's file, before it is ever a client's file.

This is the file the plan gives to `app/intakefiles.py` - where an intake's
attachments live on disk, how a caller-supplied id is gated, what the caps are,
and what `close()` deletes. None of that module exists yet. What is here now is
the one assertion that has to land *before* an anonymous caller can send a file
at all: `app/attachments.py` opens a `.docx` and an `.xlsx` with a zip reader,
and a zip reader with no bound on what it unpacks will happily turn a quarter of
a megabyte on the wire into two hundred off it.

That was tolerable while every file reaching `attachments.read` came from a
signed-in studio member uploading their own tender pack. It stops being
tolerable the moment the uploader is a stranger holding a link, which is what
the rest of this plan builds.

The bound is a sum of the sizes the archive itself declares, read from the
central directory, before any reader opens the archive. That is sound rather
than merely convenient, and the section at the bottom of this file proves the
premise it rests on rather than asserting it: CPython's `zipfile` truncates
every entry's read at the size that entry declares (`ZipExtFile._read1` does
`data = data[:self._left]`), so a declared size is an upper bound on what any
reader built on `zipfile` - python-docx and openpyxl both are - can obtain. An
archive that understates its own sizes does not get to expand past them; it
fails its CRC instead.

**Nothing here may raise into a request.** `attachments.py`'s module docstring
is explicit that a file which cannot be read is reported on the quotation as a
file that could not be read, and a client who is not in the room to be asked
makes that rule stronger, not weaker. So every refusal below is checked as an
`Attachment` carrying a `problem`, never as an exception.

    cd backend
    .venv/Scripts/python.exe scripts/check_intakefiles.py
"""

from __future__ import annotations

import io
import os
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-intakefiles-")
# Blanked for the same reason every other check script blanks them: `app.config`
# reads these once at import time via `load_dotenv(..., override=False)`, and
# this repo's real `backend/.env` names an actual Supabase project. Nothing in
# this file sends a request today, but Task 2's sections will.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""

import docx  # noqa: E402
import openpyxl  # noqa: E402

from app import attachments  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def declared_total(data: bytes) -> int:
    """What the archive says it unpacks to, summed over every entry."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return sum(entry.file_size for entry in archive.infolist())


# --- The fixtures ------------------------------------------------------------
#
# Every bomb below is a *real* office document with a real payload bolted on,
# not a hand-forged archive with lying metadata. That matters: refusing a file
# whose central directory merely claims to be enormous would prove only that
# the metadata is read. These fixtures genuinely unpack to what they declare,
# so a bound that is not there really does hand two hundred megabytes to a
# reader. They are built by streaming the payload through `ZipFile.open(...,
# "w")` a megabyte at a time rather than materialising it - the whole point is
# that a bomb is cheap to make and expensive to open, and this script should
# only pay the cheap half.

BOMB_MEGABYTES = 192
ONE_MEGABYTE = b"\0" * (1024 * 1024)


def _with_bomb(base: bytes, name: str) -> bytes:
    out = io.BytesIO(base)
    with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as archive:
        with archive.open(name, "w") as handle:
            for _ in range(BOMB_MEGABYTES):
                handle.write(ONE_MEGABYTE)
    return out.getvalue()


def plain_docx() -> bytes:
    buffer = io.BytesIO()
    document = docx.Document()
    document.add_paragraph("A scope of work, in the shape a client actually sends one.")
    document.save(buffer)
    return buffer.getvalue()


def plain_xlsx() -> bytes:
    buffer = io.BytesIO()
    book = openpyxl.Workbook()
    book.active.append(["Item", "Quantity"])
    book.active.append(["Booking module", 1])
    book.save(buffer)
    return buffer.getvalue()


PLAIN_DOCX = plain_docx()
PLAIN_XLSX = plain_xlsx()
BOMB_DOCX = _with_bomb(PLAIN_DOCX, "word/media/payload.bin")
BOMB_XLSX = _with_bomb(PLAIN_XLSX, "xl/media/payload.bin")

ok(
    "the docx bomb is small on the wire and enormous off it - the shape of the "
    "attack, not a large upload",
    len(BOMB_DOCX) < 1_000_000 and declared_total(BOMB_DOCX) > 190 * 1024 * 1024,
)
ok(
    "the xlsx bomb is the same shape",
    len(BOMB_XLSX) < 1_000_000 and declared_total(BOMB_XLSX) > 190 * 1024 * 1024,
)


# --- The controls: an ordinary document still reads --------------------------
#
# First, because a bound that refuses everything would pass every refusal test
# in this file and break the feature it is protecting.

read_docx = attachments.read("scope.docx", PLAIN_DOCX)
ok(
    "an ordinary .docx still reads, with no problem reported",
    read_docx.problem == "" and "shape a client actually sends one" in read_docx.text,
)

read_xlsx = attachments.read("items.xlsx", PLAIN_XLSX)
ok(
    "an ordinary .xlsx still reads, with no problem reported",
    read_xlsx.problem == "" and "Booking module" in read_xlsx.text,
)


# --- The bomb is refused, and reported rather than raised --------------------

bombed_docx = attachments.read("scope.docx", BOMB_DOCX)
ok(
    "a .docx that unpacks past the bound is refused",
    bombed_docx.problem != "",
)
ok(
    "and refused the way this module refuses everything - an Attachment with no "
    "text and a reason, never an exception",
    bombed_docx.text == "" and not bombed_docx.usable,
)
ok(
    "and the reason names the file, the way every other problem in this module does",
    bombed_docx.problem.startswith("scope.docx"),
)

bombed_xlsx = attachments.read("items.xlsx", BOMB_XLSX)
ok(
    "an .xlsx that unpacks past the bound is refused too - the two readers are "
    "different libraries and the bound has to sit ahead of both",
    bombed_xlsx.problem != "" and bombed_xlsx.text == "",
)


# --- Refused *rather than read*, which is the whole point --------------------
#
# The assertions above would pass if the bomb were opened, expanded to two
# hundred megabytes, and only then reported. What this task is closing is the
# expansion itself, so the claim has to be that neither reader was ever
# reached. Both `_from_docx` and `_from_xlsx` do their `import` inside the
# function body and call through the module (`docx.Document(...)`,
# `openpyxl.load_workbook(...)`), so patching the attribute on the library
# module is visible to the running reader.

_real_document = docx.Document
_real_load_workbook = openpyxl.load_workbook
opens = {"docx": 0, "xlsx": 0}


def _counting_document(*args, **kwargs):
    opens["docx"] += 1
    return _real_document(*args, **kwargs)


def _counting_load_workbook(*args, **kwargs):
    opens["xlsx"] += 1
    return _real_load_workbook(*args, **kwargs)


try:
    docx.Document = _counting_document
    openpyxl.load_workbook = _counting_load_workbook
    attachments.read("scope.docx", BOMB_DOCX)
    attachments.read("items.xlsx", BOMB_XLSX)
    ok(
        "the docx bomb never reached docx.Document at all - the bound sits ahead "
        "of the reader, not after it",
        opens["docx"] == 0,
    )
    ok(
        "and the xlsx bomb never reached openpyxl.load_workbook",
        opens["xlsx"] == 0,
    )

    # The same instrument, pointed the other way: an ordinary document must
    # still get all the way to its reader, or the two assertions above would
    # pass on a module that had simply stopped reading anything.
    attachments.read("scope.docx", PLAIN_DOCX)
    attachments.read("items.xlsx", PLAIN_XLSX)
    ok(
        "an ordinary .docx does still reach docx.Document - the counters above "
        "are measuring a refusal, not a module that reads nothing",
        opens["docx"] == 1,
    )
    ok("and an ordinary .xlsx does still reach openpyxl.load_workbook", opens["xlsx"] == 1)
finally:
    docx.Document = _real_document
    openpyxl.load_workbook = _real_load_workbook


# --- A file that is not an archive keeps the message it already had ----------
#
# The bound must not swallow the ordinary corrupt-file case. A `.docx` that is
# not a zip at all cannot be inspected for declared sizes, and the right answer
# is to let the reader fail and report what it has always reported - not to
# invent a second message for a file whose problem is that it is broken, not
# that it is large.

not_an_archive = attachments.read("scope.docx", b"This is plainly not a zip archive.")
ok(
    "a .docx that is not an archive still gets this module's existing wording, "
    "not a new one about size",
    not_an_archive.problem == "scope.docx could not be opened.",
)

truncated = attachments.read("items.xlsx", BOMB_XLSX[: len(BOMB_XLSX) // 2])
ok(
    "and a half-written archive is still just a file that could not be opened",
    truncated.problem == "items.xlsx could not be opened.",
)


# --- The bound is a bound, at its exact edge ---------------------------------
#
# Proven by moving the bound rather than by building a fixture of exactly the
# right size: the constant is read at call time, so patching it is the precise
# instrument. `PLAIN_DOCX` unpacks to a few tens of kilobytes; set the bound to
# exactly that and it must still read, set it one byte lower and it must not.

ok(
    "attachments names a bound of its own for what an archive may unpack to",
    hasattr(attachments, "MAX_UNPACKED_BYTES"),
)

if hasattr(attachments, "MAX_UNPACKED_BYTES"):
    _real_bound = attachments.MAX_UNPACKED_BYTES
    plain_total = declared_total(PLAIN_DOCX)
    try:
        attachments.MAX_UNPACKED_BYTES = plain_total
        ok(
            "an archive that unpacks to exactly the bound is read - the bound is a "
            "ceiling, not a fence one byte below it",
            attachments.read("scope.docx", PLAIN_DOCX).problem == "",
        )
        attachments.MAX_UNPACKED_BYTES = plain_total - 1
        ok(
            "and one byte past it is refused",
            attachments.read("scope.docx", PLAIN_DOCX).problem != "",
        )
    finally:
        attachments.MAX_UNPACKED_BYTES = _real_bound

    ok(
        "the real bound admits an ordinary document with room to spare",
        attachments.MAX_UNPACKED_BYTES > 16 * 1024 * 1024,
    )
    ok(
        "and refuses the bombs above",
        attachments.MAX_UNPACKED_BYTES < declared_total(BOMB_DOCX),
    )


# --- The premise the bound rests on, proven rather than assumed --------------
#
# Summing what the central directory declares is only a real bound if a reader
# cannot get more out of an entry than that entry declared. CPython's
# `ZipExtFile._read1` truncates each decompressed block to the number of bytes
# still owed against the declared size (`data = data[:self._left]`), so it
# cannot. Asserted here, against the running interpreter, so that a future
# CPython which changed that would fail this script rather than quietly leave
# `attachments.py` with a decorative bound.
#
# The fixture is a genuine fifty-megabyte entry whose declared size has been
# rewritten to a hundred bytes - the only shape that could defeat a
# metadata-based bound, if the metadata were advisory.

_honest = io.BytesIO()
with zipfile.ZipFile(_honest, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("word/document.xml", b"A" * (50 * 1024 * 1024))
_liar = _honest.getvalue().replace(
    struct.pack("<I", 50 * 1024 * 1024), struct.pack("<I", 100)
)

with zipfile.ZipFile(io.BytesIO(_liar)) as archive:
    ok(
        "the lying fixture really does understate itself",
        archive.infolist()[0].file_size == 100,
    )
    try:
        recovered = archive.read("word/document.xml")
        understated_result = f"returned {len(recovered)} bytes"
    except zipfile.BadZipFile:
        understated_result = "raised BadZipFile"

ok(
    "an archive that understates its own sizes cannot be read past them - it "
    "fails its CRC instead, so the declared sum really is an upper bound",
    understated_result == "raised BadZipFile",
)

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
