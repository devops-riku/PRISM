"""Quotations: a brief in, a priced bundle out, and everything that follows it.

The original bounded context - `POST /api/proposals` and the background job
behind it, the revision that re-scopes a saved one, the list and the single
read, the three file routes each document is served from, the jobs a studio
watches while it waits, and the studio defaults every one of them is priced
against.

Nothing here talks to a client's own door or to a proposal document built on
top of a quotation; those are their own routers. What they share lives in
`app/presentation/api/deps.py`.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Callable, List, Optional, Union

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import SQLAlchemyError

# `fastapi.UploadFile` subclasses this but is not the same class. Matching on the
# Starlette base accepts whichever one the running FastAPI hands back.
from starlette.datastructures import UploadFile as BaseUploadFile

from app.features.intakes.application import service as intakes
from app.features.jobs.application import service as jobs
from app.features.notifications.infrastructure import inbox
from app.features.quotations.application import prompts
from app.features.quotations.domain import kinds, payments, ratecard
from app.features.quotations.domain.costing import (
    CostingError,
    absorb_contingency,
    gross_for_target,
    money_decimals,
    recompute,
    snap_to_total,
)
from app.features.quotations.domain.models import (
    Estimate,
    GeneratedFile,
    ProposalBundle,
    ProposalRequest,
    PaymentTermsRecord,
    ProposalSummary,
    TierSibling,
    RevisionRequest,
)
from app.features.intakes.infrastructure import files as intakefiles
from app.features.quotations.infrastructure import repository as storage
from app.features.quotations.infrastructure.gemini import (
    GeminiConfigError,
    GeminiResponseError,
    check_brief_is_real,
    generate_estimate,
    revise_estimate,
)
from app.features.rendering.presentation import (
    format_money,
    quotation_reference,
    render_client_proposal,
    render_developer_requirements,
    render_pdf,
    render_print_html,
)
from app.features.team.infrastructure import members
from app.features.workspaces.application import settings
from app.features.workspaces.infrastructure import reference
from app.shared.infrastructure import attachments as attachments_module
from app.shared.infrastructure import config
from app.shared.presentation.http import deps

router = APIRouter()


class BriefNotRealError(RuntimeError):
    """The model was asked whether the brief describes work and said it does not.

    Its own type rather than a `GeminiResponseError`, because the two are told
    apart by what the reader should DO. A `GeminiResponseError` means the model
    could not be reached or would not answer, and the fix is to press Generate
    again in a minute. This means the model answered perfectly well and the
    answer was no, and pressing Generate again will produce the same refusal
    for ever. A queue that shows one sentence for both trains a studio to retry
    the one thing retrying cannot fix.

    Never raised when the check itself fails - see `check_brief_is_real`, which
    returns the accepting default for every error it meets rather than raising.
    """


_CURRENCY_CODES = {option.code for option in deps.CURRENCIES}


# --- Helpers -----------------------------------------------------------------


def _build_files(proposal_id: str, estimate: Estimate, revision: int = 1) -> List[GeneratedFile]:
    markdown_by_kind = {
        "proposal": render_client_proposal(estimate),
        "requirements": render_developer_requirements(estimate),
    }
    base = f"/api/proposals/{proposal_id}/files"
    return [
        GeneratedFile(
            kind=kind,
            filename=deps.download_filename(estimate, kind, revision),
            markdown=markdown_by_kind[kind],
            download_url=f"{base}/{kind}.md",
            print_url=f"{base}/{kind}.html",
            pdf_url=f"{base}/{kind}.pdf",
        )
        for kind in deps.KINDS
    ]


def _require_kind(kind: str) -> str:
    if kind not in deps.KINDS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown document '{kind}'. Valid documents are 'proposal' and 'requirements'.",
        )
    return kind


async def _read_documents(
    uploads: List[Union[UploadFile, str]] | None,
) -> List[attachments_module.Attachment]:
    """Read what the client sent as a document, and never fail the request for it.

    A quotation whose PDF could not be opened is still a quotation. Every file
    that could not be read comes back as an `Attachment` carrying the reason, and
    those reasons are reported on the finished quotation rather than raised here
    - the same rule the rate card follows when a role is missing.

    The loose typing on `uploads` is for the same reason as `_read_images`: a
    browser posts an empty file input as a part with no filename, and declaring
    `List[UploadFile]` makes FastAPI reject the whole request before any of this
    runs.
    """
    candidates = [
        upload
        for upload in (uploads or [])
        if isinstance(upload, BaseUploadFile) and (upload.filename or "").strip()
    ]
    if not candidates:
        return []

    if len(candidates) > config.MAX_DOCUMENTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(candidates)} documents attached. PRISM reads up to "
                f"{config.MAX_DOCUMENTS} - remove the rest and submit again."
            ),
        )

    read: List[attachments_module.Attachment] = []
    limit_mb = config.MAX_DOCUMENT_BYTES / (1024 * 1024)

    for upload in candidates:
        name = (upload.filename or "document").strip()
        declared = getattr(upload, "size", None)
        if declared is not None and declared > config.MAX_DOCUMENT_BYTES:
            await upload.close()
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is {declared / (1024 * 1024):.1f} MB. The limit is "
                    f"{limit_mb:.0f} MB per document."
                ),
            )

        try:
            data = await upload.read(config.MAX_DOCUMENT_BYTES + 1)
            if len(data) > config.MAX_DOCUMENT_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{name}' is larger than the {limit_mb:.0f} MB limit.",
                )
        finally:
            await upload.close()

        read.append(attachments_module.read(name, data))

    return read


async def _read_images(
    uploads: List[Union[UploadFile, str]] | None,
) -> List[tuple[bytes, str]]:
    """Validate and read the attachments. Every rejection is a 400 that names the file.

    Two shapes of "no file was chosen" reach this function and both must be
    ignored rather than rejected:
      * a browser's empty `<input type=file>` posts a part with `filename=""`,
        which Starlette turns into an UploadFile with an empty name;
      * a part with no filename header at all arrives as a plain `str`, which is
        why the parameter is typed loosely - declaring `List[UploadFile]` makes
        FastAPI reject the whole request with an unreadable 422 before any of
        this validation runs.
    """
    candidates = [
        upload
        for upload in (uploads or [])
        if isinstance(upload, BaseUploadFile) and (upload.filename or "").strip()
    ]
    if not candidates:
        return []

    if len(candidates) > config.MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(candidates)} images attached. PRISM reads up to "
                f"{config.MAX_IMAGES} - remove the rest and submit again."
            ),
        )

    images: List[tuple[bytes, str]] = []
    limit_mb = config.MAX_IMAGE_BYTES / (1024 * 1024)

    for upload in candidates:
        name = (upload.filename or "attachment").strip()
        mime = (upload.content_type or "").split(";")[0].strip().lower()

        if not mime.startswith("image/"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is {mime or 'of an unknown type'}. Attach screenshots, "
                    "sketches or photos only - PNG, JPEG, WebP, HEIC or GIF."
                ),
            )

        # Reject on the declared size before allocating anything. Without this,
        # an oversized attachment is fully materialised in memory and only then
        # refused - and MAX_IMAGES of them can arrive in one request.
        declared = getattr(upload, "size", None)
        if declared is not None and declared > config.MAX_IMAGE_BYTES:
            await upload.close()
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is {declared / (1024 * 1024):.1f} MB. The limit is "
                    f"{limit_mb:.0f} MB per image - resize it or attach a screenshot instead."
                ),
            )

        try:
            # Bounded on purpose: a part with no declared size, or one whose
            # size understates the body, must not be allowed to read past the
            # limit. One byte over is all the check below needs.
            data = await upload.read(config.MAX_IMAGE_BYTES + 1)
        except Exception as exc:
            deps.logger.warning("Could not read upload %s: %s", name, exc)
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' could not be read. Re-attach it and submit again.",
            ) from exc
        finally:
            await upload.close()

        if not data:
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is empty. Re-attach it and submit again.",
            )
        if len(data) > config.MAX_IMAGE_BYTES:
            # The read stopped one byte past the limit, so the true size is not
            # known here - say so rather than quote the cut-off as a measurement.
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is larger than the {limit_mb:.0f} MB limit per image "
                    "- resize it or attach a screenshot instead."
                ),
            )

        images.append((data, mime))

    return images


# --- The client's own files, on their way to the model -----------------------
#
# `_read_images` and `_read_documents` above read what *this request* uploaded.
# The four functions below read what a client already sent through their own
# link, off the manifest on their intake, so that pricing a client's request
# does not mean the studio re-uploading the client's files - they never make a
# second trip through anybody's browser.


def _client_file_kind(record: dict) -> str:
    """The content type of one manifest entry, clamped to what PRISM stores.

    Clamped rather than trusted, for the reason `read_intake_file` spells out
    at length where it does the same thing: `Intake.attachments` is a bare
    `List[dict]` in `ADVANCE_FIELDS` and `advance()` validates nothing about
    the five keys inside an entry, so "`/submit` is its only writer" is a
    convention rather than a check. Here the value decides which lane a file
    takes - handed to the model as image bytes, or opened by a text reader -
    and that decision has to land somewhere this module chose rather than
    somewhere a dict said. Anything unrecognised becomes `FALLBACK_TYPE`,
    which is outside `INLINE_TYPES` and so takes the document lane, where
    `attachments.read` keys on the filename's own suffix and reports whatever
    it finds.
    """
    kind = str(record.get("kind") or "")
    return kind if kind in intakefiles.CONTENT_TYPES else intakefiles.FALLBACK_TYPE


def _client_file_name(record: dict) -> str:
    """What the client called this file, cleaned again on the way back out.

    `/submit` already stored a `clean_name`d string, and this cleans it a
    second time for exactly the reason that function is public and idempotent:
    the value on the record is guaranteed by nothing (see `_client_file_kind`),
    and this one is about to be printed inside
    `attachments.describe_for_prompt`'s `--- BEGIN <name> ---` markers, in a
    prompt. A "filename" carrying a newline could close those markers early and
    write its own; `clean_name` strips control characters, so it cannot.
    """
    return intakefiles.clean_name(str(record.get("name") or ""))


def _client_lanes(records: List[dict]) -> tuple[List[dict], List[dict]]:
    """One intake's manifest, split into the two things the model reads it as.

    Images reach the model as images; everything else is opened by
    `attachments.read` and reaches it as text. `intakefiles.INLINE_TYPES` is
    the same set that decides whether the studio's own download route renders a
    file in place, and it is consulted rather than restated - a sixth raster
    added there is an image here on the same day.
    """
    images: List[dict] = []
    papers: List[dict] = []
    for record in records:
        # An entry that is not a dict, or that addresses nothing, is skipped
        # rather than reasoned about: this list is only as well-formed as
        # whatever wrote it, which is the whole point of `_client_file_kind`.
        if not isinstance(record, dict) or not str(record.get("id") or "").strip():
            continue
        if _client_file_kind(record) in intakefiles.INLINE_TYPES:
            images.append(record)
        else:
            papers.append(record)
    return images, papers


def _fit_under_studio_cap(
    records: List[dict], studio_count: int, cap: int, noun: str
) -> tuple[List[dict], str]:
    """How much of the client's set fits beside the studio's own, under the
    studio's own limit - and what has to be said about the rest.

    The combined set is bounded by `MAX_IMAGES`/`MAX_DOCUMENTS`, which are the
    studio's numbers, and never by `MAX_CLIENT_FILES`: that one is a door
    policy for a stranger holding a link and has nothing to say about what the
    studio may read once the files are on the record.

    Which of the two sets gives way turns on one question - **is there an
    action the person reading the refusal can take?** A studio member whose own
    uploads push the total over can remove one of theirs, so that is a 400 that
    names the cause and the count rather than a bare "too many". A client's
    files that overflow the cap on their own cannot be removed by anybody:
    `/submit` runs once from `issued` and there is no client-side deletion
    after it, by design, so refusing there would be a permanent dead end on a
    legitimate enquiry. That is not hypothetical - `MAX_CLIENT_FILES` is 6 and
    `MAX_DOCUMENTS` is 5, so a client who attaches six documents and no images
    is already one past it, and the studio's very first Generate would be
    refused for something nobody can fix. That case is truncated and reported,
    which is this codebase's standing preference over correcting in silence.

    So: the client's files fill the cap first, the studio's own take what is
    left, and the overflow is a 400 only when the studio's own uploads are what
    does not fit.
    """
    kept = records[:cap]
    room = cap - len(kept)
    if studio_count > room:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This request already has {len(records)} {noun} from the client, and PRISM "
                f"reads {cap} {noun} in one quotation. Remove {studio_count - room} of the "
                f"{studio_count} you attached and generate again."
            ),
        )
    if len(kept) == len(records):
        return kept, ""
    dropped = ", ".join(_client_file_name(record) for record in records[cap:])
    return kept, (
        f"the client attached {len(records)} {noun} and PRISM reads {cap} - "
        f"{dropped} did not reach the quotation"
    )


#: What is said about a file the record still names and storage no longer has.
#: Reachable in the ordinary run of things rather than only under a race:
#: `close()` deletes an intake's objects and deliberately leaves
#: `Intake.attachments` populated - the record is the history of what was sent -
#: so a closed request priced anyway names files that are all gone.
_CLIENT_FILE_MISSING = (
    "{name} is no longer stored with this request - nothing from it reached the quotation"
)

#: And what is said when the read itself came apart. Distinct from the sentence
#: above because they are different facts about the file: one says the record
#: outlived the object, the other says something went wrong reaching for it.
_CLIENT_FILE_UNREADABLE = (
    "{name} could not be read from storage - nothing from it reached the quotation"
)


def _stage_name(name: str, limit: int = 30) -> str:
    """A client's filename, cut to fit a progress line.

    Cut in the MIDDLE rather than the end, because the end is where the
    extension is and "reading site-photo-2026-07-30-13…" tells a studio less
    than "reading site-photo-20…-102.jpg". The stage line is one line on a
    strip, and a name that wraps it moves everything under it.
    """
    clean = " ".join(str(name or "").split()) or "a file"
    if len(clean) <= limit:
        return clean
    keep = limit - 1
    head = keep // 2
    return clean[:head] + "…" + clean[-(keep - head):]


def _load_client_files(
    intake_id: str,
    image_records: List[dict],
    document_records: List[dict],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[List[tuple[bytes, str]], List[attachments_module.Attachment], List[str]]:
    """The bytes behind one intake's manifest. Blocking, and called in a thread.

    Every line of this is blocking and most of it is network: with Spaces
    configured, `intakefiles.read` is a `list_objects_v2` to recover the stored
    extension plus a `get_object` for the bytes, per file, and six files is
    twelve round trips. `attachments.read` then opens a PDF or unpacks a zip,
    which is CPU-bound and can be seconds on a large one. That is why this is a
    plain `def` reached through `asyncio.to_thread` - the same idiom
    `_store_client_files` and `read_intake_file` already use, and for the same
    reason: a blocking socket call on the event loop parks every request this
    worker holds, including an anonymous client's `/submit`, which cannot even
    be routed while it is parked.

    `to_thread` runs the call in a copy of the current context, so the
    workspace `_gate` set for the request - carried into the background task by
    `asyncio.create_task`, which copies the context the same way - is what this
    thread reads under. That matters more than it looks: `workspaces.current()`
    falls back to `default_id()` when it is unset rather than raising, so a
    borrow that failed to carry would read the *first workspace on file*,
    silently, and a single-workspace test could not tell the difference.
    check_intake_gate.py proves it across three, positively and negatively.

    **Nothing here raises, and the per-record guard below is what makes that
    true** rather than a claim about the two calls inside it. This runs inside
    `run()`, whose own `except Exception` fails the job and stamps
    `QUOTE_FAILED` - so a single malformed manifest entry escaping this loop
    would lose the whole quotation, which is the exact opposite of the rule
    this function exists to keep. `attachments.read` is already total by
    contract and `intakefiles.read` catches broadly on both backends, but
    `intakefiles.read`'s local branch reaches `workspaces.root()`, which raises
    `NoWorkspace`, and neither of those contracts is enforced from here. One
    file is what a bad entry may cost.

    Back come the images as `(bytes, content type)` pairs, the documents as
    `Attachment`s - `attachments.read`'s own shape, carrying its own `problem`
    when there was one - and the sentences that need saying to whoever pressed
    Generate.
    """
    images: List[tuple[bytes, str]] = []
    papers: List[attachments_module.Attachment] = []
    problems: List[str] = []

    #: One flat walk over both lanes, so the guard is written once rather than
    #: twice. Images first, matching `create_proposal`'s own reading order.
    lanes = [(record, True) for record in image_records]
    lanes += [(record, False) for record in document_records]

    for position, (record, as_image) in enumerate(lanes, start=1):
        name = _client_file_name(record)
        # Before the read, not after: the point of the line is to say what is
        # being waited ON. With Spaces configured this file is two network
        # round trips away and `attachments.read` may then unpack a zip, which
        # is the second or two the studio is looking at a static strip for.
        #
        # Called from inside the worker thread. `jobs.stage` writes under
        # `jobs._lock`, a module-level RLock, so that is safe - and it is why
        # this is a callback rather than this function importing `jobs` and
        # deciding what to say, which would put wording for a progress strip
        # inside the function that reads bytes.
        if on_progress is not None:
            on_progress(position, len(lanes), name)
        papers_before = len(papers)
        try:
            found = intakefiles.read(intake_id, str(record.get("id") or ""))
            if found is None:
                deps.logger.warning(
                    "Intake %s: %s is named on the record and is not in storage",
                    intake_id,
                    name,
                )
                problem = _CLIENT_FILE_MISSING.format(name=name)
            elif as_image:
                # The manifest's kind rather than what storage reported, for the
                # reason `read_intake_file` gives: it is the value `/submit`
                # resolved through `intakefiles.resolve_type` and the one the
                # studio's queue already shows this file as. It is inside
                # `INLINE_TYPES` or this record would not be in this lane.
                images.append((found[0], _client_file_kind(record)))
                problem = ""
            else:
                # The client's own filename, not the stored `<12-hex>.<ext>`:
                # `attachments.read` picks its reader off the suffix, and the
                # name is what appears between the markers in the prompt and in
                # any problem line the studio reads afterwards.
                #
                # This also settles what happens to the manifest's `note`, which
                # may already hold an extraction warning from upload time: it is
                # not read off the record, because this call *recomputes* it.
                # `_store_client_files` produced that note as
                # `attachments.read(name, data).problem` over the same bytes
                # under the same name, and this is the same call - so for any
                # entry whose `kind` is still the one `/submit` resolved, the
                # warning reaches the quotation by being derived again rather
                # than copied, and the two cannot drift apart. An entry whose
                # `kind` was altered to something `_client_file_kind` has to
                # clamp lands in this lane instead of the image one and is
                # reported on its suffix, which is a different sentence and the
                # safe direction to differ in.
                item = attachments_module.read(name, found[0])
                papers.append(item)
                problem = item.problem
        except Exception:  # noqa: BLE001 - one bad entry must not cost the quotation
            deps.logger.exception(
                "Intake %s: %s could not be read for the quotation", intake_id, name
            )
            # Anything half-appended for this record goes with it, so a partial
            # read cannot reach the model as though it were whole.
            del papers[papers_before:]
            problem = _CLIENT_FILE_UNREADABLE.format(name=name)

        if not problem:
            continue
        problems.append(problem)
        # A document with nothing behind it still gets an `Attachment` carrying
        # the reason - the shape `describe_for_prompt` already knows to leave
        # out, exactly as it leaves out a scan with no text layer - but only
        # when the reader did not already produce one saying the same thing.
        if not as_image and len(papers) == papers_before:
            papers.append(attachments_module.Attachment(name, "", "", problem))

    return images, papers, problems


def _normalise_brief(raw: str) -> str:
    """The studio's own brief, and it carries the same floor the client's scope
    does - which it did not until now.

    The floor arrived on the client's door first because that door opens once
    and a bad write costs the link. That made it easy to argue the studio
    needed nothing: a studio can read what comes back and try again. But the
    thing it tries again on is a full generation - several model calls, a
    minute of wall time, and a bundle persisted in SQL - and `"a"` bought all of
    it. Free to check, so it is checked.

    What this does NOT catch is prose that is structurally fine and means
    nothing: `erwerasdad dklajdlaksdjacsdasd` is thirty characters, thirteen
    distinct letters and two words. Only meaning separates that from a real
    brief, and meaning is `_brief_is_real`'s job, inside the job where a model
    call belongs. This is the cheap half, and it runs first because it is free
    and it is certain.
    """
    brief = (raw or "").strip()
    if not brief:
        raise HTTPException(
            status_code=400,
            detail="Describe the job before submitting - PRISM prices what the brief says.",
        )
    if len(brief) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The brief is {len(brief):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it or attach the detail as images."
            ),
        )
    shortfall = deps.scope_shortfall(brief)
    if shortfall:
        raise HTTPException(status_code=400, detail=shortfall)
    return brief


def _normalise_budget_hint(raw: str) -> str:
    """`budget_hint` is free text the model reasons about, same as `brief` -
    it just never had `brief`'s ceiling. Bounded here with the same constant
    and the same shape of error, not a second convention, because a client's
    words reaching a prompt need one length rule, not one per field."""
    hint = (raw or "").strip()
    if len(hint) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The budget note is {len(hint):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it rather than pasting the whole thread."
            ),
        )
    return hint


def _apply_ceiling(estimate: Estimate, ceiling: float) -> tuple[Estimate, bool, str]:
    """Hold a quotation to a price it must not exceed.

    A ceiling is not a target. A quotation that already fits is returned
    untouched - padding scope up to a budget is how a client ends up paying for
    work nobody chose - and only one that came in over is brought down.

    Bringing it down uses the same solver a target total does, so the figure is
    exact and the rates survive: the effort moves, not the price of a day.

    Returns `(estimate, applied, note)`. It reports rather than raises: this now
    runs once per tier inside a background job, and one tier that cannot be
    capped is a note on that tier, not a reason to throw away the other two.
    """
    if ceiling <= 0 or estimate.cost.total <= ceiling:
        return estimate, False, ""

    over = estimate.cost.total - ceiling
    step = 10.0 ** -money_decimals(estimate.currency)

    # Not every figure is reachable - with a tax percentage applied the totals
    # a quotation can produce skip roughly one value in nine - so `snap_to_total`
    # returns the nearest, which may be a centavo *above*. A target may round
    # either way; a maximum may not. Walk down until it is genuinely under.
    aim = ceiling
    for _ in range(4):
        try:
            snapped = snap_to_total(estimate, aim)
        except CostingError as exc:
            deps.logger.warning("Could not cap a quotation at %.2f: %s", ceiling, exc)
            return estimate, False, str(exc)
        if snapped.achieved <= ceiling + step / 2:
            break
        # Step down from the aim, not from what it achieved. Deriving the next
        # aim from the result lands on the same unreachable figure again and the
        # loop makes no progress.
        aim -= step
    else:  # pragma: no cover - four steps is far more than the lattice needs
        note = (
            f"This quotation could not be brought under {ceiling:,.2f}. Raise the cap or cut "
            "scope in the brief."
        )
        deps.logger.warning(note)
        return estimate, False, note

    deps.logger.warning(
        "Quotation came in %.2f over the %.2f cap and was brought down to %.2f",
        over,
        ceiling,
        snapped.achieved,
    )
    return snapped.estimate, True, ""


def _normalise_tiers(raw: str) -> List[str]:
    """Parse 'Basic, Standard, Extended' into distinct tier names.

    One name is not a tier - it is a single quotation with a label - so it is
    treated as none at all. The cap is the point past which somebody has pasted
    the wrong thing into the field.
    """
    names: List[str] = []
    for part in (raw or "").replace("\n", ",").split(","):
        name = " ".join(part.split())[:40]
        if name and name.casefold() not in {existing.casefold() for existing in names}:
            names.append(name)

    if len(names) > 6:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That is {len(names)} tiers. Six is the most PRISM will price in one pass - "
                "each is a full quotation and a separate call."
            ),
        )
    return names if len(names) > 1 else []


def _with_parent_ref(bundle: ProposalBundle) -> ProposalBundle:
    """Attach the printed reference of the bundle this one was revised from.

    `parent_id` is storage - a hex string nobody has ever said out loud. What a
    reader needs is the number on the quotation it came from, which is the thing
    the client already has in their inbox.

    Resolved here rather than stored so the quotations prepared before this
    existed show it too, and left empty when the parent has been deleted: the
    id still links, because a dead link is a better answer than inventing a
    reference for a document that is gone.
    """
    if not bundle.parent_id:
        return bundle

    parent = storage.get(bundle.parent_id)
    if parent is None:
        return bundle

    filled = bundle.model_copy(deep=True)
    filled.parent_ref = (parent.estimate.quotation_ref or "").strip()
    return filled


def _with_siblings(bundle: ProposalBundle) -> ProposalBundle:
    """Attach the other tiers quoted from the same brief.

    Resolved on read rather than stored, because a sibling's total changes when
    it is revised and a copy written at creation time would go stale silently.
    """
    if not bundle.tier_group_id:
        return bundle

    siblings = [
        TierSibling(
            id=other.id,
            tier_name=other.tier_name,
            tier_index=other.tier_index,
            total=other.estimate.cost.total,
            currency=other.estimate.currency,
        )
        for other in storage.all_bundles()
        if other.tier_group_id == bundle.tier_group_id
    ]
    siblings.sort(key=lambda item: (item.tier_index, item.tier_name))

    filled = bundle.model_copy(deep=True)
    filled.tier_siblings = siblings
    return filled


def _payment_terms(request: ProposalRequest) -> payments.PaymentTerms:
    """Build the terms from the form, and refuse a schedule that does not total 100.

    A written schedule arrives as a JSON string because multipart has no nested
    types. Both failure modes - malformed JSON and a total that is not 100 - are
    400s naming what is wrong, because both are something the sender can fix.
    """
    rows: List[payments.ScheduleRow] = []
    raw = (request.payment_schedule or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="The payment schedule was not readable. Send it as a JSON array of "
                '{"percent": number, "trigger": text} objects.',
            ) from exc
        if not isinstance(parsed, list):
            raise HTTPException(
                status_code=400,
                detail="The payment schedule must be a list of payments.",
            )
        for entry in parsed:
            if not isinstance(entry, dict):
                raise HTTPException(
                    status_code=400,
                    detail="Every payment in the schedule must name a percent and a trigger.",
                )
            rows.append(
                payments.ScheduleRow(
                    percent=float(entry.get("percent") or 0.0),
                    trigger=str(entry.get("trigger") or ""),
                )
            )

    try:
        return payments.PaymentTerms(
            deposit_pct=request.deposit_pct,
            instalments=request.instalments,
            cadence=request.payment_cadence,
            deposit_trigger=request.deposit_trigger or "Signed statement of work",
            schedule=rows,
        ).validated()
    except payments.TermsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _normalise_flag(raw: str) -> bool:
    """A checkbox arrives as whatever the client felt like sending."""
    return (raw or "").strip().lower() in {"1", "true", "yes", "on", "inclusive"}


def _normalise_target(raw: str, currency: str) -> float:
    """Parse a target total typed by a human.

    People type money the way they read it, so thousands separators, a currency
    symbol and stray spaces are all accepted. An empty field means no target,
    which is a valid revision - "drop the SMS work" needs no number.
    """
    text = (raw or "").strip()
    if not text:
        return 0.0

    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    try:
        value = float(cleaned)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{text}' is not a number. Enter the total you want in {currency}, "
                "for example 3000000 or 3,000,000."
            ),
        ) from None

    if value <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"A target total has to be greater than zero. Got '{text}'.",
        )
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail=f"'{text}' is not a usable number.")
    return value


def _apply_target(estimate: Estimate, target: float | None) -> tuple[Estimate, bool, str]:
    """Land `estimate` on `target`, and report honestly what it hit.

    Shared by both endpoints: a target total means the same thing whether it was
    set on the first quotation or on a revision. The model re-scopes to roughly
    the figure and this settles the arithmetic exactly.

    The typed figure is read on the same basis as the rates. Tax-exclusive, it
    is the price of the work and the tax is added on top of it; inclusive, or
    with no tax at all, it is the total itself. Solving a net target as though
    it were the gross total quietly delivers the client less work than they
    asked to buy, by exactly the tax rate.

    Returns the estimate, whether the target was hit exactly, and a note that is
    empty unless something needs saying.
    """
    if target is None:
        return estimate, True, ""

    model_total = estimate.cost.total
    goal = gross_for_target(estimate, target)
    try:
        snapped = snap_to_total(estimate, goal)
    except CostingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    note = snapped.note

    # When tax was added on top, say so: the figure on the documents is not the
    # figure that was typed, and a reader deserves to see the arithmetic rather
    # than wonder which of the two numbers is wrong.
    cost = snapped.estimate.cost
    if goal != target and cost.tax_pct > 0:
        label = cost.tax_label or "tax"
        added = (
            f"The target of {target:,.2f} is the price of the work. With {label} at "
            f"{cost.tax_pct:g}% on top, the quotation totals {goal:,.2f}."
        )
        note = f"{added} {note}".strip() if note else added

    # A model that ignored the target leaves the arithmetic stretching a scope
    # that was never costed for the figure. Say so rather than shipping quietly.
    if model_total > 0:
        drift = goal / model_total
        if drift < 0.7 or drift > 1.4:
            stretch = (
                f"The scoped work came to {model_total:,.2f} and was scaled by {drift:.2f}x to "
                f"reach {goal:,.2f}. Check that the line items still describe work worth "
                f"that figure."
            )
            note = f"{note} {stretch}".strip() if note else stretch
            deps.logger.warning("Estimate scaled by %.2fx to meet a target of %.2f", drift, goal)

    return snapped.estimate, snapped.exact, note


# --- Endpoints ---------------------------------------------------------------


@router.post("/api/proposals", response_model=jobs.JobView, status_code=202, tags=["proposals"])
async def create_proposal(
    brief: str = Form(...),
    kind: str = Form("software"),
    kind_label: str = Form(""),
    currency: str = Form("PHP"),
    client_name: str = Form(""),
    project_name: str = Form(""),
    market_region: str = Form("Philippines"),
    budget_hint: str = Form(""),
    timeline_hint: str = Form(""),
    target_total: str = Form(""),
    tax_inclusive: str = Form(""),
    tax_mode: str = Form(""),
    deposit_pct: str = Form(""),
    instalments: str = Form(""),
    payment_cadence: str = Form(""),
    deposit_trigger: str = Form(""),
    payment_schedule: str = Form(""),
    tiers: str = Form(""),
    tier_ceiling: str = Form(""),
    pricing_basis: str = Form(""),
    #: Which client intake this quotation is being prepared for, if any. Blank
    #: for every caller that predates intakes - the pad included, until Stage 2
    #: wires it through - and for the pad's own "quick quote" path, which has
    #: no client request behind it at all.
    intake_id: str = Form(""),
    images: List[Union[UploadFile, str]] = File(default=[]),
    documents: List[Union[UploadFile, str]] = File(default=[]),
) -> jobs.JobView:
    """One brief in, one job back.

    Preparing a quotation takes the better part of a minute and three tiers take
    a minute and a half, so the work happens behind the request. Everything that
    can be rejected - a bad currency, a schedule that does not total 100, a
    target above its ceiling - is still rejected here, synchronously, before a
    job exists. Only the part that takes time is deferred.

    `target_total` is optional and binding: the scope is written towards it and
    the arithmetic is then solved onto it exactly. `budget_hint` stays what it
    was - free text the model reasons about, with no arithmetic attached.
    """
    code = deps.normalise_currency(currency)
    request = ProposalRequest(
        brief=_normalise_brief(brief),
        currency=code,
        client_name=(client_name or "").strip(),
        project_name=(project_name or "").strip(),
        market_region=(market_region or "").strip() or "Philippines",
        budget_hint=_normalise_budget_hint(budget_hint),
        timeline_hint=(timeline_hint or "").strip(),
        target_total=_normalise_target(target_total, code),
        # Both are accepted: the pad sends the mode, and `tax_inclusive` is what
        # every earlier caller sends. ProposalRequest reconciles them, with the
        # mode winning when it is one of the three it knows.
        tax_mode=(tax_mode or "").strip().lower(),
        tax_inclusive=_normalise_flag(tax_inclusive),
        deposit_pct=_normalise_target(deposit_pct, code) if deposit_pct.strip() else 0.0,
        instalments=int(_normalise_target(instalments, code)) if instalments.strip() else 3,
        payment_cadence=(payment_cadence or "monthly").strip().lower(),
        deposit_trigger=(deposit_trigger or "").strip(),
        payment_schedule=(payment_schedule or "").strip(),
        tiers=(tiers or "").strip(),
        tier_ceiling=_normalise_target(tier_ceiling, code) if tier_ceiling.strip() else 0.0,
        pricing_basis=(pricing_basis or "rate_card").strip().lower(),
    )
    # The kind travels beside the request rather than on it: `ProposalRequest`
    # is what the work is priced from, and the discipline decides what the
    # second document says. `resolve` never raises, so an id nobody knows - or
    # none at all, from a caller older than kinds - takes the software path this
    # endpoint has always taken, rather than a 400 on a field nobody sent.
    kind_id = kinds.resolve(kind).id
    typed_kind = (kind_label or "").strip()
    target = request.target_total if request.target_total > 0 else None
    ceiling = request.tier_ceiling if request.tier_ceiling > 0 else 0.0
    if target and ceiling and target > ceiling:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The target cost of {target:,.2f} is above the {ceiling:,.2f} ceiling. "
                "A quotation cannot land on a figure it is not allowed to reach - raise the "
                "ceiling or lower the target."
            ),
        )
    studio = settings.load()
    # The card is the studio's default, but a quotation can be priced from the
    # work instead - a client outside the usual bands, a fixed-price bid, a job
    # somebody else is delivering. Choosing "requirements" takes the card out of
    # play entirely rather than half-applying it.
    card = studio.rate_card if request.pricing_basis != "requirements" else []
    card_text = ratecard.describe_for_prompt(card, request.currency)
    # The working day is stated per role inside the card itself now, so there
    # is no studio-wide sentence left to send.
    basis_text = ""
    terms = _payment_terms(request)
    terms_text = payments.describe_for_prompt(terms)
    attachments = await _read_images(images)
    # Read to text here, once, before any tier is generated: five tiers must
    # not mean opening the same PDF five times.
    papers = await _read_documents(documents)

    # What the client themselves attached, when this quotation is being
    # prepared for a request that came in through a link. Split here and
    # fetched later, and the split is the cheap half: it reads the manifest on
    # the record, which `intakes.get` has already taken off local disk, so the
    # counts the caps below turn on cost no network at all and the refusal they
    # can produce still happens synchronously, before a job exists, exactly as
    # this endpoint's docstring promises.
    #
    # An `intake_id` naming nothing this workspace holds - a stale form, a
    # request in somebody else's workspace, an id somebody typed - is not an
    # error here, for the same reason `stamp` refuses to make it one. And
    # `intakes.get` is what enforces the workspace rather than a comparison
    # written here: it builds its path under `workspaces.root()`, so an intake
    # belonging to another workspace is simply not a file.
    client_images: List[dict] = []
    client_papers: List[dict] = []
    client_notes: List[str] = []
    if intake_id:
        for_intake = intakes.get(intake_id)
        if for_intake is not None:
            client_images, client_papers = _client_lanes(for_intake.attachments)
            client_images, note = _fit_under_studio_cap(
                client_images, len(attachments), config.MAX_IMAGES, "images"
            )
            if note:
                client_notes.append(note)
            client_papers, note = _fit_under_studio_cap(
                client_papers, len(papers), config.MAX_DOCUMENTS, "documents"
            )
            if note:
                client_notes.append(note)

    #: What `prepare()` actually sends, as distinct from what this request
    #: uploaded. Both are initialised to the studio's own files so that every
    #: path through `run()` produces a valid prompt whether or not there is an
    #: intake behind it; `run()` merges the client's in, in a thread, before the
    #: first tier is priced.
    model_images: List[tuple[bytes, str]] = list(attachments)
    papers_text = attachments_module.describe_for_prompt(papers)

    tiers = _normalise_tiers(request.tiers)

    async def prepare(
        tier: str, index: int, above_total: float = 0.0, above_name: str = ""
    ) -> Estimate:
        # `_apply_request_identity` makes the request authoritative over the
        # project name, so asking the model to append the tier is not enough -
        # it would be overwritten. The name is built per tier here instead, and
        # only when the caller supplied one to build on.
        per_tier = request
        if tier and request.project_name.strip():
            per_tier = request.model_copy(
                update={"project_name": f"{request.project_name.strip()} - {tier}"}
            )
        spec = (
            prompts.TierSpec(
                name=tier,
                index=index,
                names=tiers,
                above_total=above_total,
                above_name=above_name,
            )
            if tiers
            else None
        )
        estimate = await generate_estimate(
            per_tier,
            model_images,
            card_text,
            basis_text,
            terms_text,
            not studio.show_contingency_to_client,
            spec,
            ceiling,
            documents_text=papers_text,
            kind=kind_id,
            # The studio's own word for the discipline. Without it, "something
            # else" is guidance telling the model to use a name it was never
            # given.
            kind_label=typed_kind,
        )
        # Stamped the moment it comes back, before the costing and the renderers
        # see it: the kind is what the studio chose, not something the model is
        # asked to agree with, and a model that answered "software" to an
        # accounting brief would otherwise decide which document gets written.
        # Every tier of a ladder is the same discipline - one brief, one kind.
        estimate.kind = kind_id
        estimate.kind_label = typed_kind
        return estimate

    job = jobs.create(
        kind="quotation",
        title=request.project_name or request.client_name or "Quotation",
        detail=" · ".join(
            part
            for part in (
                request.client_name if request.project_name else "",
                f"{len(tiers)} tiers: {', '.join(tiers)}" if tiers else "",
                f"Ceiling {ceiling:,.0f} {code}" if ceiling else "",
            )
            if part
        ),
        # In execution order, which is top-down: each tier is priced against the
        # figure the one above came back with, so the top is priced first. A
        # step list in ladder order would tick "Pricing Basic" while Extended
        # was the thing actually running.
        steps=[f"Pricing {tier}" for tier in reversed(tiers)] or ["Pricing the work"],
    )

    def stamp(to: str, **fields) -> None:
        """Move the intake this quotation came from, if it came from one.

        Never raises: a quotation is the thing being prepared, and losing the
        bookkeeping around it must not lose the quotation. An intake_id that
        does not resolve is a stale form, not a reason to refuse the work.
        Caught broadly rather than just `intakes.IntakeError`, because
        `intakes.advance` reaches `workspaces.root()` on its way to a file on
        disk and that can raise `NoWorkspace` or a bare `OSError` - neither is
        an `IntakeError`. A narrower catch would let either escape `stamp`
        itself, and the two call sites fail differently if it does: at the
        PREPARING call there is no enclosing `try` in `run()` yet, so `run()`
        would die before `jobs.start` ever executes and the job would sit at
        `queued` forever with no `jobs.fail` and no notification; at the
        QUOTED call, which runs after `jobs.finish` has already recorded
        success, it would fall into `run()`'s own `except Exception` and
        report a quotation that was saved as one that failed. `IntakeError`'s
        message is self-describing and logged as-is; anything else is logged
        with its type and stack, since a plain %s of those loses both.

        A move to `QUOTE_FAILED` is also told to the admins - a request that
        failed silently is one nobody comes back to. That announcement sits
        after this `try`, not inside it, and carries its own `except`: the
        client asked, and the quotation failed, whether or not the move above
        also landed, so it must not be skipped just because `advance` raised;
        and `intakes.get` below reaches `workspaces.root()` exactly as
        `advance` does, so it is exactly as capable of raising `NoWorkspace`
        - `inbox.notify` itself is verified never to (everything in it runs
        inside one broad `except Exception` that returns 0 rather than
        raise), but the lookup that builds its words is not, and this is
        still `stamp`: nothing past this line may reach `run()`.
        """
        if not intake_id:
            return
        try:
            intakes.advance(intake_id, to, **fields)
        except intakes.IntakeError as exc:
            deps.logger.warning("Intake %s not moved to %s: %s", intake_id, to, exc)
        except Exception:
            deps.logger.exception("Intake %s could not be moved to %s", intake_id, to)

        if to == intakes.QUOTE_FAILED:
            try:
                entry = intakes.get(intake_id)
                inbox.notify(
                    "intake.quote_failed",
                    inbox.ADMINS,
                    {
                        "title": "A client request could not be quoted",
                        "body": " - ".join(
                            part
                            for part in (
                                (entry.client_email if entry else ""),
                                str(fields.get("error", "")),
                            )
                            if part
                        ),
                        "href": "#/intakes",
                    },
                )
            except Exception:
                deps.logger.exception(
                    "Intake %s: could not notify admins of a failed quote", intake_id
                )

    async def run() -> None:
        nonlocal papers_text
        stamp(intakes.PREPARING, job_id=job.id)
        # "Scope", not "brief", because that is what the field is called on
        # both screens this job can start from - the pad's own step is headed
        # "What is in scope" and the client's is "Scope". A stage naming a word
        # that appears nowhere in front of the person reading it makes them
        # wonder which of the things they typed it means.
        jobs.start(job.id, "Reading the Scope")
        try:
            # Before anything is priced. The structural floor in
            # `_normalise_brief` already refused the empty, the too-short and
            # the visibly-mashed; what reaches here and is still not a brief is
            # text that only MEANS nothing - "erwerasdad dklajdlaksdjacsdasd"
            # is thirty characters, thirteen distinct letters and two words,
            # and no rule short of reading it can tell it from a real one.
            #
            # Inside the job rather than on the endpoint, and that is a
            # deliberate reading of this route's own contract two comments
            # below: "everything that can be rejected is rejected
            # synchronously." The rejectable part is the floor, and it IS
            # synchronous. This is not rejectable without a network call, and
            # `POST /api/proposals` makes none today - adding one would mean a
            # Gemini outage stopped a studio from STARTING a quotation rather
            # than failing a job they can retry, on a blocking call in an
            # `async def` handler, which is the shape that parks the event loop
            # for every other request this worker holds.
            #
            # One call, on the brief alone. The client's files are fetched
            # after this, so a refused brief costs no Spaces round trips either.
            if config.CHECK_BRIEF_IS_REAL:
                verdict = await check_brief_is_real(request.brief)
                if not verdict.is_brief:
                    raise BriefNotRealError(
                        verdict.reason
                        or "That does not read as a description of work. Say what needs "
                        "building, who it is for, and anything that shapes it."
                    )
            if client_images or client_papers:
                # Fetched here - inside the job, and inside a thread - rather
                # than before the job was handed off, and the reason is what the
                # alternative costs. With Spaces configured this is two round
                # trips per file against a client bounded at 5s connect / 10s
                # read with two attempts, and botocore makes two TCP connection
                # attempts per attempt: Task 2 measured 18 seconds of wall time
                # for a single operation against a packet-dropping endpoint.
                # Six files is twelve operations. As a wait on the POST that is
                # a Generate button hanging for minutes with no job to look at
                # and nothing to distinguish it from a crash; as a wait inside
                # the job it is a progress bar saying what it is doing. This
                # endpoint's own contract already picks between those two:
                # everything that can be *rejected* is rejected synchronously,
                # and only the part that takes time is deferred. The caps are
                # the part that can be rejected, and they were answered off the
                # manifest above at no network cost at all.
                #
                # `asyncio.to_thread` in either case, because this is `async
                # def` and a blocking socket call on the loop parks every
                # request this worker holds - see `_load_client_files`.
                total_files = len(client_images) + len(client_papers)
                jobs.stage(
                    job.id,
                    f"Reading {total_files} file{'' if total_files == 1 else 's'} from the client",
                )
                found_images, found_papers, problems = await asyncio.to_thread(
                    _load_client_files,
                    intake_id,
                    client_images,
                    client_papers,
                    lambda at, total, name: jobs.stage(
                        job.id, f"Reading {_stage_name(name)} ({at} of {total})"
                    ),
                )
                # The client's files first in both lanes, which is a decision
                # rather than an accident. `attachments.describe_for_prompt`
                # spends one `MAX_TOTAL_CHARS` budget in list order and names
                # whatever it runs out of room for, so first is what is
                # guaranteed to be read: the client's documents are the request
                # being priced, the studio's are reference material added on top
                # of it, and a quotation prepared without the client's own scope
                # is the failure this whole feature exists to prevent. The cost
                # is the reverse of that and is real - a studio attaching a long
                # document to a request already carrying 60,000 characters of
                # client material may see it named in `describe_for_prompt`'s
                # own "was not included" line rather than read.
                model_images[:0] = found_images
                papers_text = attachments_module.describe_for_prompt(found_papers + papers)
                client_notes.extend(problems)
            if tiers:
                # One call per tier, top down, one after another. They used to
                # run together - three tiers in the time of the slowest - and
                # the ladder came back in whatever order the model happened to
                # land in, because no call could see what any other had priced.
                # Twice in a row the middle tier undercut the entry tier.
                #
                # Each tier is now told what the tier above actually cost, which
                # only exists if the tier above has already been priced. That
                # costs the concurrency; background jobs are what makes the
                # trade affordable, and it is why they were built first.
                estimates = [None] * len(tiers)
                above_total = 0.0
                above_name = ""

                for position, index in enumerate(reversed(range(len(tiers)))):
                    tier = tiers[index]
                    # Said before the call rather than after it. `jobs.step`
                    # below marks the tier DONE, which is the right thing for a
                    # progress bar and the wrong thing for the line of text
                    # above it - between two steps that line was describing
                    # work that had already finished.
                    jobs.stage(job.id, f"Pricing {tier} ({position + 1} of {len(tiers)})")
                    estimate = await prepare(tier, index, above_total, above_name)
                    estimates[index] = estimate
                    # The anchor for the next tier down is what this one came
                    # back with. It is a raw figure - the rate card and the
                    # payment terms have not run yet - so it guides the model
                    # rather than binding it; `_finalise` does the binding, on
                    # the finished totals.
                    above_total = estimate.cost.total
                    above_name = tier
                    # Marked when the tier is genuinely back, so the bar reports
                    # work done rather than time passed.
                    jobs.step(job.id, position, f"{tier} priced")
            else:
                # The single-quotation path, and the longest wait in the job:
                # one model call, tens of seconds, previously behind whatever
                # stage happened to be showing when it started - which for a
                # request with attachments was the last filename read.
                jobs.stage(job.id, "Pricing the work")
                estimates = [await prepare("", 0)]
                jobs.step(job.id, 0, "Costing")

            jobs.stage(job.id, "Costing and writing the documents")
            bundles = _finalise(
                estimates, request, tiers, target, ceiling, studio, card, terms
            )
            jobs.finish(job.id, [bundle.id for bundle in bundles])
            stamp(
                intakes.QUOTED,
                bundle_ids=[bundle.id for bundle in bundles],
                priced_scope=brief,
                priced_budget=budget_hint,
            )
            first = bundles[0]
            # A quotation that had to be stretched, held down or could not
            # reach its target is still ready - and saying only "ready" would
            # be the silent correction this whole system exists to avoid.
            # These live on the bundle, not the estimate: they describe what the
            # server had to do to the quotation, not what it says.
            caveats = [note for note in (first.target_note, first.tier_cap_note) if note]
            if first.target_total and not first.hit_target and not caveats:
                caveats.append("the target total could not be reached exactly")
            # A client's file that did not reach the model goes ahead of the
            # arithmetic notes rather than after them, because only `caveats[0]`
            # reaches the body below: a document that was not read changes what
            # was priced, which is worth more of that one line than a note about
            # what the costing had to do to a figure. Placed after the block
            # above, not folded into it, so it cannot suppress the "could not be
            # reached exactly" caveat merely by making the list non-empty.
            #
            # Joined into one entry rather than added as several, for the same
            # reason: six separate entries would name one missing file and drop
            # the other five in silence, which is the failure this whole line
            # exists to avoid. Bounded by `MAX_CLIENT_FILES` plus one truncation
            # note per lane.
            if client_notes:
                caveats.insert(0, "; ".join(client_notes))
            inbox.notify(
                "quotation_ready",
                inbox.ACTOR,
                {
                    "title": (
                        ("Quotation ready" if len(bundles) == 1 else f"{len(bundles)} tiers ready")
                        + (" - worth a look" if caveats else "")
                    ),
                    "body": " · ".join(
                        part
                        for part in (
                            first.estimate.project_name,
                            first.estimate.client_name,
                            format_money(first.estimate.cost.total, first.estimate.currency),
                        )
                        if part
                    )
                    + (f" - {caveats[0]}" if caveats else ""),
                    "href": f"#/q/{first.id}",
                },
            )
        except BriefNotRealError as exc:
            # Not `logger.error`: nothing is broken. A studio typed something
            # that is not a brief and was told so, which is this check working.
            deps.logger.info("Brief refused before pricing: %s", exc)
            jobs.fail(job.id, str(exc))
            stamp(intakes.QUOTE_FAILED, error=str(exc))
        except GeminiConfigError as exc:
            deps.logger.error("Generation blocked by configuration: %s", exc)
            jobs.fail(job.id, str(exc))
            stamp(intakes.QUOTE_FAILED, error=str(exc))
        except GeminiResponseError as exc:
            deps.logger.error("Unusable Gemini response: %s | snippet=%s", exc, exc.snippet)
            jobs.fail(job.id, str(exc))
            stamp(intakes.QUOTE_FAILED, error=str(exc))
            inbox.notify(
                "quotation_failed",
                inbox.ACTOR,
                {
                    "title": "That quotation did not come back",
                    "body": f"{exc} Rephrase the brief and try again.",
                    "href": "#/pad",
                },
            )
        except HTTPException as exc:
            jobs.fail(job.id, str(exc.detail))
            stamp(intakes.QUOTE_FAILED, error=str(exc.detail))
            # Late by nature: the costing solver only runs after the model call,
            # so an unreachable target is a ninety-second answer to a number
            # somebody typed. Say which number, and where to change it.
            inbox.notify(
                "quotation_rejected",
                inbox.ACTOR,
                {
                    "title": "That quotation could not be priced as asked",
                    "body": str(exc.detail),
                    "href": "#/pad",
                },
            )
        except Exception:  # pragma: no cover - genuinely unexpected
            deps.logger.exception("Unhandled failure while generating an estimate")
            jobs.fail(
                job.id,
                "The quotation could not be prepared. The error is in the API log.",
            )
            stamp(
                intakes.QUOTE_FAILED,
                error="The quotation could not be prepared. The error is in the API log.",
            )
            # Both, and in different words. The person waiting needs to know
            # their work is gone and that somebody has been told; the admins
            # need the part only they can act on.
            inbox.notify(
                "quotation_crashed",
                inbox.ACTOR,
                {
                    "title": "That quotation failed unexpectedly",
                    "body": "Nothing was saved. The admins have been told - try again.",
                    "href": "#/pad",
                },
            )
            inbox.notify(
                "quotation_crashed",
                inbox.ADMINS,
                {
                    "title": "A quotation failed unexpectedly",
                    "body": "The reason is in the API log. Nothing was saved.",
                    "href": "#/jobs",
                },
            )

    # Held so the loop cannot garbage-collect a running task out from under us.
    task = asyncio.create_task(run())
    deps.BACKGROUND.add(task)
    task.add_done_callback(deps.BACKGROUND.discard)

    return jobs.view(job)


def _finalise(
    estimates: List[Estimate],
    request: ProposalRequest,
    tiers: List[str],
    target: float | None,
    ceiling: float,
    studio,
    card,
    terms,
) -> List[ProposalBundle]:
    """Turn raw estimates into saved, costed, document-bearing bundles.

    Split out of the endpoint when generation moved to the background: the HTTP
    handler now returns a job before any of this runs, so everything that used
    to live after the Gemini call had to become something the worker can call.

    Tiers are finalised from the top down, and that order is the point. Every
    other pass - the rate card, the payment terms, the contingency, the target -
    moves the total, so the only figure a tier can honestly be held below is the
    *finished* total of the tier above it. The top tier is held to the studio
    cap; each tier under it is held below whatever the one above ended up at.
    The bundles still come back in ladder order.
    """
    group_id = storage.new_id() if tiers else ""
    prepared: dict[int, ProposalBundle] = {}

    # Top tier first when there is a ladder; a single quotation is its own top.
    order = list(reversed(range(len(estimates)))) if tiers else list(range(len(estimates)))
    # References are drawn in ladder order even though the tiers are finalised
    # top down, so Basic is the lower number. A client reading "Standard is
    # 0000001 and Basic is 0000002" would reasonably assume they arrived in that
    # order, and they did not.
    references = [
        reference.build(studio.reference_prefix, studio.reference_mode) for _ in estimates
    ]
    # The studio cap opens the chain. With no cap the top tier is unconstrained
    # and still sets the maximum for everything below it, which is how the order
    # holds even when nobody named a budget.
    cap = ceiling

    for index in order:
        estimate = estimates[index]
        # The card binds before costing: `recompute` derives every subtotal from
        # quantity x unit_rate, and `snap_to_total` only ever moves quantities,
        # so a rate set here is the rate the documents print.
        estimate, removed, bound, removed_value = ratecard.apply(estimate, card)
        estimate, terms_applied = payments.apply(estimate, terms)
        if not studio.show_contingency_to_client:
            estimate = absorb_contingency(estimate)
        estimate = recompute(estimate)
        estimate, hit_target, note = _apply_target(estimate, target)
        # The cap is enforced last, after every other pass has had its say. An
        # exact target is a stronger instruction than a maximum, so a quotation
        # with both is already at its target and left alone - and a ladder built
        # onto one target total is a ladder of identical figures, which is why a
        # target switches the chain off rather than fighting it.
        is_top = not tiers or index == len(estimates) - 1
        # The studio's cap is typed on the same basis as the rates, so it needs
        # the same reading as a target: exclusive of tax, the tax goes on top of
        # it. The caps further down the ladder are finished totals of the tier
        # above and are already gross - converting those would raise every lower
        # tier by the tax rate and break the order this chain exists to keep.
        effective_cap = 0.0 if target else (gross_for_target(estimate, cap) if is_top else cap)
        estimate, cap_applied, cap_note = _apply_ceiling(estimate, effective_cap)

        # For the top tier the cap is the studio ceiling and being brought down
        # to it is the ceiling doing its job. For the others it is the tier
        # above, and being brought down to it is the ladder being put in order.
        ceiling_applied = cap_applied and is_top
        order_enforced = cap_applied and not is_top

        if tiers and not target:
            step = 10.0 ** -money_decimals(estimate.currency)
            # Strictly below, by one minor unit. No invented margin: a
            # percentage gap would be money removed from a client's quotation on
            # a rule nobody agreed to. Separation between tiers is the model's
            # job, done through scope; this only guarantees the order.
            cap = max(estimate.cost.total - step, step)

        # The reference the documents print. Taken once and stored on the
        # estimate: a number that changed between the markdown and the PDF would
        # not be a reference at all. Tiers each take their own, so three
        # quotations from one brief are three numbers rather than one repeated.
        estimate.quotation_ref = references[index]

        proposal_id = storage.new_id()
        bundle = ProposalBundle(
            id=proposal_id,
            created_at=storage.utc_now_iso(),
            estimate=estimate,
            files=_build_files(proposal_id, estimate),
            revision=1,
            root_id=proposal_id,
            target_total=target or 0.0,
            hit_target=hit_target,
            target_note=note,
            rate_card_bound=bound,
            rate_card_removed=removed,
            rate_card_removed_value=removed_value,
            payment_terms=PaymentTermsRecord(**terms.model_dump()),
            payment_terms_applied=terms_applied,
            tier_ceiling=ceiling,
            ceiling_applied=ceiling_applied,
            tier_cap=effective_cap,
            tier_order_enforced=order_enforced,
            tier_cap_note=cap_note,
            priced_from_rate_card=bool(card),
            tier_group_id=group_id,
            tier_name=tiers[index] if tiers else "",
            tier_index=index,
        )
        storage.save(bundle)
        prepared[index] = bundle

        deps.logger.info(
            "Quotation %s prepared%s: %s %.2f across %d line items, target=%s, exact=%s, "
            "cap=%s, order_enforced=%s",
            proposal_id,
            f" [{bundle.tier_name}]" if bundle.tier_name else "",
            estimate.currency,
            estimate.cost.total,
            len(estimate.line_items),
            f"{target:.2f}" if target is not None else "none",
            hit_target,
            f"{effective_cap:.2f}" if effective_cap else "none",
            order_enforced,
        )

    # Finalised top-down, returned in ladder order: everything downstream - the
    # tier switcher, the sibling list, the job's result_ids - reads left to
    # right the way the client does.
    return [prepared[index] for index in sorted(prepared)]


@router.post(
    "/api/proposals/{proposal_id}/revise",
    response_model=jobs.JobView,
    status_code=202,
    tags=["proposals"],
)
async def revise_proposal(
    proposal_id: str,
    instruction: str = Form(""),
    target_total: str = Form(""),
) -> jobs.JobView:
    """Re-scope an existing quotation, optionally onto an exact total.

    A revision is a new bundle with a new id, not an edit. The parent is left
    untouched so a sent quotation stays exactly as it was sent, and the two can
    be compared row by row.

    The currency is inherited from the parent. There is no way to change it
    here, because re-pricing the same work in another currency is an exchange
    rate by another name and this system never converts.
    """
    parent = deps.require_bundle(proposal_id)
    request = RevisionRequest(
        instruction=deps.normalise_instruction(instruction),
        target_total=_normalise_target(target_total, parent.estimate.currency),
    )
    target = request.target_total if request.target_total > 0 else None

    if not request.instruction and target is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Say what to change, or set a target total. A revision with neither "
                "would just reprice the same brief."
            ),
        )

    job = jobs.create(
        kind="revision",
        title=f"Revision of {parent.estimate.project_name or parent.id}",
        detail=request.instruction[:120] or (f"Target {target:,.2f}" if target else ""),
        steps=["Re-scoping the work", "Costing and writing the documents"],
    )

    async def run() -> None:
        jobs.start(job.id, "Reading the quotation")
        try:
            studio = settings.load()
            card_text = ratecard.describe_for_prompt(studio.rate_card, parent.estimate.currency)
            basis_text = ""
            terms = payments.PaymentTerms(**parent.payment_terms.model_dump())
            terms_text = payments.describe_for_prompt(terms)

            revised = await revise_estimate(
                parent.estimate,
                request.instruction,
                target,
                card_text,
                basis_text,
                terms_text,
                # The discipline goes to the model as well as onto the estimate.
                # Set only afterwards, it would re-scope an accounting engagement
                # as software and hand back a document with its sections empty.
                kind=parent.estimate.kind,
                kind_label=parent.estimate.kind_label,
            )
            jobs.step(job.id, 0, "Costing and writing the documents")

            # The kind is inherited and cannot be changed by a revision. It is
            # what the document was written as, and re-scoping is an argument
            # about scope and price - an instruction that turned an accounting
            # engagement into a software one would be a different quotation
            # wearing the same reference.
            revised.kind = parent.estimate.kind
            revised.kind_label = parent.estimate.kind_label

            revised, removed, bound, removed_value = ratecard.apply(revised, studio.rate_card)
            # Terms are inherited, not re-asked: a revision that quietly changed
            # the payment schedule the client has already seen would be a
            # different offer.
            revised, terms_applied = payments.apply(revised, terms)
            if not studio.show_contingency_to_client:
                revised = absorb_contingency(revised)
            revised = recompute(revised)
            revised, hit_target, note = _apply_target(revised, target)

            revision_id = storage.new_id()
            revision_number = parent.revision + 1
            # A revision keeps the number the client already has, marked as a
            # later version of it. Issuing a fresh reference would leave two
            # unrelated-looking quotations for one conversation.
            parent_ref = (parent.estimate.quotation_ref or "").split(" R")[0].strip()
            revised.quotation_ref = (
                f"{parent_ref} R{revision_number}"
                if parent_ref
                else reference.build(studio.reference_prefix, studio.reference_mode)
            )
            bundle = ProposalBundle(
                id=revision_id,
                created_at=storage.utc_now_iso(),
                estimate=revised,
                files=_build_files(revision_id, revised, revision_number),
                revision=revision_number,
                parent_id=parent.id,
                root_id=parent.root_id or parent.id,
                revision_instruction=request.instruction,
                target_total=target or 0.0,
                hit_target=hit_target,
                target_note=note,
                rate_card_bound=bound,
                rate_card_removed=removed,
                rate_card_removed_value=removed_value,
                payment_terms=PaymentTermsRecord(**terms.model_dump()),
                payment_terms_applied=terms_applied,
                # A revision stays in whatever tier group its parent belongs to.
                tier_group_id=parent.tier_group_id,
                tier_name=parent.tier_name,
                tier_index=parent.tier_index,
            )
            storage.save(bundle)

            deps.logger.info(
                "Revision %s of %s (r%d) prepared: %s %.2f, target=%s, exact=%s",
                revision_id,
                parent.id,
                revision_number,
                revised.currency,
                revised.cost.total,
                f"{target:.2f}" if target is not None else "none",
                hit_target,
            )
            jobs.finish(job.id, [bundle.id])
            inbox.notify(
                "revision_ready",
                inbox.ACTOR,
                {
                    "title": "Revision ready",
                    "body": " · ".join(
                        part
                        for part in (
                            bundle.estimate.quotation_ref,
                            bundle.estimate.project_name,
                            format_money(bundle.estimate.cost.total, bundle.estimate.currency),
                        )
                        if part
                    ),
                    "href": f"#/q/{bundle.id}",
                },
            )
        except GeminiConfigError as exc:
            deps.logger.error("Revision blocked by configuration: %s", exc)
            jobs.fail(job.id, str(exc))
        except GeminiResponseError as exc:
            deps.logger.error("Unusable Gemini revision: %s | snippet=%s", exc, exc.snippet)
            jobs.fail(job.id, str(exc))
            # Worth saying plainly: the original is deliberately untouched, so
            # silence would leave somebody believing a revised version exists
            # when only the original does.
            inbox.notify(
                "revision_failed",
                inbox.ACTOR,
                {
                    "title": "That revision did not come back",
                    "body": f"{exc} The original quotation is untouched.",
                    "href": "#/quotations",
                },
            )
        except HTTPException as exc:
            jobs.fail(job.id, str(exc.detail))
        except Exception:  # pragma: no cover - genuinely unexpected
            deps.logger.exception("Unhandled failure while revising an estimate")
            jobs.fail(job.id, "The revision could not be prepared. The error is in the API log.")

    task = asyncio.create_task(run())
    deps.BACKGROUND.add(task)
    task.add_done_callback(deps.BACKGROUND.discard)

    return jobs.view(job)


def _summarise(bundle: ProposalBundle) -> ProposalSummary:
    estimate = bundle.estimate
    base = f"/api/proposals/{bundle.id}/files"
    return ProposalSummary(
        id=bundle.id,
        created_at=bundle.created_at,
        quotation_ref=quotation_reference(estimate),
        project_name=estimate.project_name,
        client_name=estimate.client_name,
        currency=estimate.currency,
        total=estimate.cost.total,
        line_items=len(estimate.line_items),
        revision=bundle.revision,
        parent_id=bundle.parent_id,
        root_id=bundle.root_id or bundle.id,
        target_total=bundle.target_total,
        hit_target=bundle.hit_target,
        tax_label=estimate.cost.tax_label,
        tax_pct=estimate.cost.tax_pct,
        tax_inclusive=estimate.cost.tax_inclusive,
        tier_group_id=bundle.tier_group_id,
        tier_name=bundle.tier_name,
        proposal_url=f"{base}/proposal.md",
        requirements_url=f"{base}/requirements.md",
    )


@router.get("/api/proposals", response_model=List[ProposalSummary], tags=["admin"])
async def list_proposals(
    q: str = "",
    limit: int = 200,
) -> List[ProposalSummary]:
    """Every quotation on disk, newest first.

    `q` matches the project, the client, the printed reference or the id,
    case-insensitively - enough to find one quotation among a few hundred
    without a search index. The reference is included because it is the number
    on the document, and so the one somebody is holding when they search.
    """
    needle = (q or "").strip().lower()
    bounded = max(1, min(int(limit or 200), 1000))

    rows = [_summarise(bundle) for bundle in storage.all_bundles()]
    if needle:
        rows = [
            row
            for row in rows
            if needle in row.project_name.lower()
            or needle in row.client_name.lower()
            or needle in row.quotation_ref.lower()
            or needle in row.id.lower()
        ]
    return rows[:bounded]


@router.get("/api/proposals/{proposal_id}", response_model=ProposalBundle, tags=["proposals"])
async def get_proposal(proposal_id: str) -> ProposalBundle:
    return _with_parent_ref(_with_siblings(deps.require_bundle(proposal_id)))


@router.delete("/api/proposals/{proposal_id}", tags=["admin"])
async def delete_proposal(proposal_id: str) -> Response:
    """Delete one quotation and both of its documents. Not recoverable.

    Revisions are independent bundles, so deleting a parent leaves its revisions
    intact and their `parent_id` pointing at an id that no longer resolves. That
    is deliberate - a sent revision should not disappear because the draft it
    came from was tidied away.
    """
    if not storage.is_valid_id(proposal_id):
        raise HTTPException(status_code=404, detail=f"No quotation with id {proposal_id!r}.")
    if not storage.delete(proposal_id):
        raise HTTPException(status_code=404, detail=f"No quotation with id {proposal_id!r}.")
    return Response(status_code=204)


@router.get("/api/proposals/{proposal_id}/files/{kind}.md", tags=["files"])
async def download_markdown(proposal_id: str, kind: str) -> Response:
    bundle = deps.require_bundle(proposal_id)
    generated = storage.file_for(bundle, _require_kind(kind))
    if generated is None:
        raise HTTPException(status_code=404, detail=f"This quotation has no '{kind}' document.")

    return Response(
        content=generated.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{generated.filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/proposals/{proposal_id}/files/{kind}.html", tags=["files"])
async def printable_html(proposal_id: str, kind: str) -> HTMLResponse:
    """Print-ready HTML. The browser's own print dialog is the PDF exporter."""
    bundle = deps.require_bundle(proposal_id)
    kind = _require_kind(kind)
    markdown = storage.markdown_for(bundle, kind)
    if markdown is None:
        raise HTTPException(status_code=404, detail=f"This quotation has no '{kind}' document.")

    # `kind` is authoritative. Left to infer the sheet from the title, the
    # renderer matches on words like "developer" or "requirement", so a project
    # merely *named* one of those would print the client proposal as the
    # developer duplicate.
    html = render_print_html(
        markdown,
        deps.document_title(bundle.estimate, kind),
        bundle.estimate,
        kind=kind,
    )
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@router.get("/api/proposals/{proposal_id}/files/{kind}.pdf", tags=["files"])
async def download_pdf(proposal_id: str, kind: str) -> Response:
    """The document as a PDF file, for attaching to an email.

    Rendered from the same markdown as every other version of it, so there is
    one source of content and no chance of the PDF saying something the web
    page does not.
    """
    bundle = deps.require_bundle(proposal_id)
    kind = _require_kind(kind)
    markdown = storage.markdown_for(bundle, kind)
    if markdown is None:
        raise HTTPException(status_code=404, detail=f"This quotation has no '{kind}' document.")

    try:
        data = render_pdf(
            markdown,
            deps.document_title(bundle.estimate, kind),
            bundle.estimate,
            kind=kind,
        )
    except Exception as exc:  # pragma: no cover - a renderer bug, not user input
        deps.logger.exception("PDF rendering failed for %s/%s", proposal_id, kind)
        raise HTTPException(
            status_code=500,
            detail=(
                "The PDF could not be produced. The error is in the API log; the "
                "markdown and print views of this quotation still work."
            ),
        ) from exc

    filename = deps.download_filename(bundle.estimate, kind, bundle.revision).removesuffix(".md") + ".pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/jobs", response_model=List[jobs.JobView], tags=["jobs"])
async def list_jobs(limit: int = 50) -> List[jobs.JobView]:
    """Everything prepared or preparing, newest first."""
    return jobs.listing(limit)


@router.get("/api/jobs/{job_id}", response_model=jobs.JobView, tags=["jobs"])
async def get_job(job_id: str) -> jobs.JobView:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"No job with the reference {job_id!r}.",
        )
    return jobs.view(job)


def _with_reference_preview(defaults: settings.StudioDefaults) -> settings.StudioDefaults:
    """Attach what the next quotation number will look like.

    Read-only and computed on the way out, so the panel can show the studio its
    own numbering without taking a number from the counter to do it.
    """
    return defaults.model_copy(
        update={
            "reference_preview": reference.preview(
                defaults.reference_prefix, defaults.reference_mode
            ),
            "proposal_reference_preview": reference.preview(
                defaults.proposal_prefix,
                defaults.proposal_reference_mode,
                series=reference.PROPOSALS,
            )
        }
    )


def _tell_the_team_what_changed(was, now) -> None:
    """Say what a save actually changed, to the people it changes things for.

    Only the settings that reach a document: rates bind and are enforced,
    numbering is what a client quotes back, terms are printed word for word.
    The rest - a market region, a contingency preference - is somebody tidying,
    and telling a team about tidying is how a feed stops being read.

    An admin gets a link to Settings. A member gets the same news and no link,
    because that door would refuse them.
    """
    changes: List[str] = []

    def card(defaults):
        return [
            (entry.role.strip().lower(), entry.unit, round(entry.rate, 2))
            for entry in defaults.rate_card or []
        ]

    if card(was) != card(now):
        count = len(now.rate_card or [])
        changes.append(
            f"the rate card ({count} {'role' if count == 1 else 'roles'} in {now.currency})"
        )
    if len(was.policies or []) != len(now.policies or []) or [
        (clause.title, clause.body) for clause in was.policies or []
    ] != [(clause.title, clause.body) for clause in now.policies or []]:
        changes.append("the proposal terms")
    if (was.reference_prefix, was.reference_mode) != (now.reference_prefix, now.reference_mode):
        changes.append("how quotations are numbered")
    if (was.tax_mode, was.tax_inclusive) != (now.tax_mode, now.tax_inclusive):
        changes.append("the tax basis")

    if not changes:
        return

    what = changes[0] if len(changes) == 1 else ", ".join(changes[:-1]) + " and " + changes[-1]
    who = (inbox.identity()[0] or "Somebody").split("@")[0]

    inbox.notify(
        "settings_changed",
        inbox.TEAM,
        lambda role, you: {
            "title": (
                f"You changed {what}" if you else f"{who} changed {what}"
            ),
            "body": "Quotations prepared from now on use it. Anything already on file is unchanged.",
            "href": "#/settings" if role == members.ADMIN else "",
        },
    )


@router.get("/api/settings", response_model=settings.StudioDefaults, tags=["admin"])
async def get_settings() -> settings.StudioDefaults:
    return _with_reference_preview(settings.load())


@router.put("/api/settings", response_model=settings.StudioDefaults, tags=["admin"])
async def put_settings(defaults: settings.StudioDefaults) -> settings.StudioDefaults:
    """Set what a new brief form opens with.

    These prefill the form and nothing else. They are not sent to the model and
    they do not override its judgement - see the module docstring in
    app/settings.py for what is deliberately absent and why.
    """
    code = deps.normalise_currency(defaults.currency)
    if code not in _CURRENCY_CODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{code}' is not one of the currencies PRISM offers. "
                f"Pick one of: {', '.join(sorted(_CURRENCY_CODES))}."
            ),
        )
    defaults.currency = code

    # Read before the save, because save() replaces the cache first and the
    # question worth answering is what CHANGED - "settings were saved" is a
    # notification about a button press, which nobody needs.
    was = settings.load()

    try:
        saved = settings.save(defaults)
    except SQLAlchemyError as exc:
        deps.logger.error("Could not persist studio defaults: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "The defaults could not be saved to the database. Check the "
                "database connection, then save again."
            ),
        ) from exc

    _tell_the_team_what_changed(was, saved)
    return _with_reference_preview(saved)
