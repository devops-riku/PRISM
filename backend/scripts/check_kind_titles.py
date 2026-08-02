"""Does the requirements document carry the title of its kind wherever it prints?

Three places name the second document: the HTML page title, the label stamped on
every PDF page, and `main._document_title`, which the browser tab and the PDF
metadata both take. An accounting engagement must be called accounting in all
three, and a quotation with no kind must still read exactly as it always has -
that is what every quotation prepared before kinds existed is.

The markdown body is a stub on purpose. The document's own H1 is
`renderers/markdown.py`'s to print; this script is about the furniture around
it, and `kind="requirements"` is authoritative for both renderers, so no body
can move the branch being checked.

    "…/backend/.venv/Scripts/python.exe" scripts/check_kind_titles.py
"""

from __future__ import annotations

import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pypdf import PdfReader

from app.main import _document_title
from app.renderers import render_pdf, render_print_html
from app.schemas import Estimate

BODY = "## The engagement\n\nTwelve months of ledgers, reconciled and closed.\n"

failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        failures.append(name)


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def pdf_title(data: bytes) -> str:
    meta = PdfReader(io.BytesIO(data)).metadata
    return str((meta or {}).get("/Title", ""))


def run(heading: str, estimate: Estimate, expected: str, forbidden: str) -> None:
    print(heading)

    # No title supplied: the fallback is the only thing that can name this page,
    # which is the line under test. A title passed here would short-circuit it.
    html = render_print_html(BODY, "", estimate, kind="requirements")
    check("html <title>", f"<title>{expected}</title>" in html, f"expected {expected!r}")
    check(
        "html carries no other kind",
        forbidden.lower() not in html.lower(),
        f"{forbidden!r} absent",
    )

    title = _document_title(estimate, "requirements")
    check("_document_title", title == f"Ledger cleanup · {expected}", repr(title))

    data = render_pdf(BODY, title, estimate, kind="requirements")
    text = pdf_text(data)
    # The running label is stamped upper-case, so both sides are folded - a
    # case-sensitive test here would pass against any label at all.
    check("pdf page label", expected.lower() in text.lower(), f"expected {expected!r}")
    check(
        "pdf carries no other kind",
        forbidden.lower() not in text.lower(),
        f"{forbidden!r} absent",
    )
    # Not a check on the kind - `title` is what was just passed in. It confirms
    # the title reaches the file's metadata, which is what a mail client shows.
    check("pdf metadata carries the document title", pdf_title(data) == title, repr(pdf_title(data)))
    print()


accounting = Estimate(
    project_name="Ledger cleanup",
    client_name="Northwind Trading",
    kind="accounting",
)

# Built without a `kind` key at all, which is what a bundle written before kinds
# existed reads back as. Setting kind="software" by hand would test the default
# rather than its absence.
no_kind = Estimate.model_validate(
    {"project_name": "Ledger cleanup", "client_name": "Northwind Trading"}
)

run("accounting bundle", accounting, "Accounting Requirements", "Developer requirements")
run("bundle with no kind", no_kind, "Developer requirements", "Accounting Requirements")

if failures:
    print(f"FAILED: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
