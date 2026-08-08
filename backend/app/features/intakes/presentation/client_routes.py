"""The client's own door - the one bounded context a stranger touches.

Six routes on `/api/client/{token}`, every one of them answered with no
`Authorization` header at all, resolved by the token alone. Four of the
section banners `main.py` grew around them came across with the code and are
reproduced below exactly where they fell; they are the argument for why this
is safe, and they say it better than a summary would.

One thing those banners describe is deliberately NOT in this file. The
rate limit and the body-size cap that guard the three write routes - and
every name behind them, `_CLIENT_WRITE_ROUTES` through `_enforce_rate_limit`
- live in `app/presentation/api/middleware.py` beside `_gate`, the middleware that calls
them. They hold module-level mutable state: a second copy of
`_rate_limit_hits` living here would be a second table nothing reconciles
with the first, which is a limit of twenty that silently admits forty.
`_gate` matches on the URL's own last path segment rather than on a resolved
route, so it goes on guarding these three the moment this router is included
- as long as the path strings below are the ones it already knows.

The banner at "The client writes" still says those names are "defined here,
beside the routes they describe". That sentence was true when every route in
this app lived in one file, and it is the one thing this move made false. It
is left standing rather than reworded because the REASON it gives - that a
control belongs where a reader can see what it guards - is still the reason,
and it is worth knowing that the split cost something. What it cost is this:
the guard for these three routes is now one file away from them, and nothing
in this file will fail if somebody renames a path below out from under it.
"""

from __future__ import annotations

import asyncio
import re
from typing import Callable, List, NamedTuple, Union

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

# `fastapi.UploadFile` subclasses this but is not the same class. Matching on the
# Starlette base accepts whichever one the running FastAPI hands back.
from starlette.datastructures import UploadFile as BaseUploadFile

from app.features.intakes.application import client_view as clientview
from app.features.intakes.application import service as intakes
from app.features.intakes.infrastructure import files as intakefiles
from app.features.intakes.infrastructure import tokens
from app.features.notifications.infrastructure import inbox
from app.features.quotations.domain import kinds
from app.features.quotations.domain.models import Estimate
from app.features.quotations.infrastructure import repository as storage
from app.features.quotations.infrastructure.gemini import check_brief_is_real
from app.features.rendering.presentation import (
    format_money,
    render_pdf,
    render_print_html,
)
from app.features.team.infrastructure import members
from app.features.workspaces.application import settings
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import attachments as attachments_module
from app.shared.infrastructure import config
from app.shared.presentation.http import deps

router = APIRouter()


#: The full C0 control range plus DEL - deliberately including the tab,
#: line-feed and carriage-return that `.strip()` alone only ever catches at
#: the two ends of a string, not in the middle. None of the seven are
#: legitimate inside an email address or a phone number - unlike `scope` or
#: `budget_text`, free text where a line break is exactly the point, these
#: two fields have no shape a stray newline could ever be part of - and
#: otherwise ride along verbatim into a studio's own screens
#: (`GET /api/intakes`) and, for the email, into `clientview._masked`, which
#: returns a string with no `@` in it back unmasked. Stripped rather than
#: rejected outright: a stray control character pasted in from somewhere is
#: a nuisance to clean up, not intent worth a 400 for.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _normalise_scope(raw: str) -> str:
    """An intake's `scope` reaches the same prompt a brief does, so it carries
    the same ceiling - and, since Stage 2 made this an anonymous write, a floor
    the studio's own brief does not have.

    The asymmetry was backwards before this. `_normalise_brief` refuses an
    empty brief with an actionable sentence; this, on the LESS trusted door,
    accepted the empty string outright - the browser blocked it and curl did
    not. And `/submit` runs once from `issued` with no move back, so whatever
    arrives here is not a draft, it is the client's entire request. A scope of
    "a" produced a full priced quotation, because there is no path in the
    generation pipeline that can decline: `response_schema=Estimate` is forced
    and `schemas.py` gives the model no field in which to say it cannot price
    something. The refusal has to happen at the door or nowhere.
    """
    scope = (raw or "").strip()
    if len(scope) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The scope is {len(scope):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it or attach the detail separately."
            ),
        )
    shortfall = deps.scope_shortfall(scope)
    if shortfall:
        raise HTTPException(status_code=400, detail=shortfall)
    return scope


def _normalise_budget_text(raw: str) -> str:
    """The client's own budget words, bounded the same way `scope` is - see
    `_normalise_scope` - and required to carry a number.

    The field is a target cost. "around 300k", "under ₱500,000" and "2.5M" all
    say something a studio can shape a quotation against; "a" does not, and
    neither does "not sure". It was already a required field, so this only
    makes the requirement mean what it says. `str.isdigit()` rather than an
    ASCII test, so Arabic-Indic and full-width digits count as the numbers
    they are.
    """
    text = (raw or "").strip()
    if len(text) > config.MAX_BRIEF_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The budget note is {len(text):,} characters. The limit is "
                f"{config.MAX_BRIEF_CHARS:,} - summarise it rather than pasting the whole thread."
            ),
        )
    if config.MIN_CLIENT_BUDGET_DIGITS:
        digits = sum(1 for character in text if character.isdigit())
        if digits < config.MIN_CLIENT_BUDGET_DIGITS:
            raise HTTPException(
                status_code=400,
                detail="Put a rough number on it - “around 300k” or “under ₱500,000”.",
            )
    return text


#: `scope` and `budget_text` reach a prompt and so inherited `MAX_BRIEF_CHARS`
#: as their ceiling; an address and a phone number never do; 254 is the
#: practical maximum length of an email address (RFC 5321 §4.5.3.1.3) and
#: comfortably covers a phone number with a country code, extension and
#: punctuation besides.
_MAX_CONTACT_CHARS = 254


def _normalise_client_email(raw: str) -> str:
    """The client's own address, from `/api/client/{token}/submit` - the
    first anonymous write in this codebase, where every other field on the
    same request is length-checked and this one, before this function
    existed, was not. Scrubbed of control characters and bounded the same
    way `scope`/`budget_text` are, not validated as an actual email address:
    `create_intake` (the studio's own route) does not validate the shape
    either, and this function's job is only to make the value safe to store
    and later render on the studio's own screens, not to police what counts
    as an address."""
    email = _CONTROL_CHARS.sub("", raw or "").strip()
    if len(email) > _MAX_CONTACT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That email address is {len(email):,} characters. The limit is "
                f"{_MAX_CONTACT_CHARS}."
            ),
        )
    return email


def _normalise_client_phone(raw: str) -> str:
    """The client's own phone number - see `_normalise_client_email`, which
    this mirrors exactly."""
    phone = _CONTROL_CHARS.sub("", raw or "").strip()
    if len(phone) > _MAX_CONTACT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That phone number is {len(phone):,} characters. The limit is "
                f"{_MAX_CONTACT_CHARS}."
            ),
        )
    return phone


# --- The client's own door (Stage 2 Tasks 3-4) --------------------------------
#
# Everything below this line, until the next section, is reachable with no
# `Authorization` header at all - see the `open_path` comment in `_gate` for
# what makes that safe. Nothing above this line changes: every route already
# defined still runs behind the gate exactly as it did before this file grew
# a second kind of caller.

#: What `/api/client/{token}` answers when the token does not currently work -
#: never minted, expired, relinked away, or naming an intake that is now
#: `closed`. One literal, reused for all four, because they are supposed to be
#: indistinguishable to whoever is holding the link: see `tokens.resolve`'s and
#: `clientview.of`'s own docstrings for why a stranger must not be able to tell
#: "never existed" from "used to work" by the wording of the answer.
_CLIENT_LINK_GONE = "That link is not valid, or has expired."


@router.get("/api/client/{token}", response_model=None, tags=["client"])
async def read_client_view(token: str) -> dict:
    """What a client sees of their own request - answered to whoever holds the
    link, which is the point, exactly as `read_invite` answers to whoever
    holds an invitation.

    `response_model=None`, set explicitly rather than left to the bare `-> dict`
    annotation: since FastAPI 0.89, a return annotation *is* the response
    model unless told otherwise, and a bare `dict` response model round-trips
    through FastAPI's own validation/serialisation pass rather than being
    returned exactly as `clientview.of` built it - the one boundary this
    route exists to keep a raw, hand-filtered dict on the safe side of. What
    `clientview.of` returns depends on the intake's state - `issued` carries
    two fields, a sent quotation carries a dozen - and `app/schemas.py` is
    deliberately not extended to describe a client's view (see
    `clientview.py`'s own docstring), so there is no single shape to declare
    here even if this route wanted one.

    `X-Workspace` is never read here. `tokens.resolve` is the only thing
    that says which workspace a link belongs to; trusting a header instead
    would let anybody hand in a token of their own alongside a workspace id
    that is not theirs and read whichever one they named.
    """
    found = tokens.resolve(token)
    if found is None:
        raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        entry = intakes.get(intake_id)
        if entry is None:
            # `tokens.resolve` already re-read the intake once before handing
            # this back, so reaching `None` here is the same transient-read
            # race its own docstring describes, not a case it failed to rule
            # out - and it answers exactly as an unknown token would either
            # way.
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)
        if entry.state == intakes.CLOSED:
            # Cannot be delegated to the token having gone blank in SQL -
            # `tokens.resolve` validated against *its own* read of this same
            # record, moments earlier and inside its own `borrow`/`give_back`,
            # not this one. The two reads can straddle a `close()` landing in
            # between: `resolve()` sees a live, unexpired token and hands back
            # a match; by the time this handler's own `intakes.get()` runs,
            # `close()` has already flipped the state and blanked the token
            # via `intakes._write`. Nothing upstream of this line has checked
            # `entry.state` at all. A second, independent reason this cannot
            # be delegated: `tokens._build_locked` re-indexes any intake with
            # a non-empty token with no state check, by its own docstring's
            # admission - so a restored backup, a hand-edit, or any future
            # writer that bypasses `intakes._write` would resolve a live
            # token on a closed intake forever, not just across one race
            # window. Refused here, explicitly, with the same body every
            # other kind of "gone" gets - `clientview.of` itself would answer
            # `{"state": "closed"}` for this, correctly, for the *studio's*
            # reading of a closed intake; a client holding the link is not
            # the studio, and Task 3's promise is that this and "never
            # existed" are the same answer.
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)
        bundle = storage.get(entry.sent_bundle_id) if entry.sent_bundle_id else None
        # `clientview.of` runs inside this `try`, still borrowed - not after
        # `give_back` - because it calls `settings.load()`, which reads
        # `workspaces.current()`. Built outside the borrow, this would render
        # with whatever workspace `_gate`'s own `workspaces.use(X-Workspace)`
        # left the context pointed at, not the one the token actually names.
        try:
            return clientview.of(entry, bundle)
        except ValueError:
            # `clientview.of` raises rather than show a state it does not
            # recognise (`proposal_sent`, today - unreachable until Stage 3)
            # or a quoted-face state with no bundle attached. That is a bug on
            # this server's side of the line, not a client's business to see
            # the traceback of: it still answers with the one body a bad
            # token gets, not a 500 that would at least confirm the token
            # named something real.
            deps.logger.exception("clientview.of could not show intake %s", intake_id)
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE) from None
    finally:
        workspaces.give_back(borrowed)


# --- The client writes (Stage 2 Task 4) ---------------------------------------
#
# Three routes, and every one of them is a stranger's POST answered with no
# `Authorization` header - see the `open_path` comment in `_gate` for the three
# things that make that safe. This section is the second of them: each
# handler below enforces its own state check rather than trusting the gate,
# which admits every method under this prefix and could not tell a legal
# write from an illegal one if it tried.
#
# The rate limit and the body-size check that guard these three routes are
# defined here, beside the routes they describe, but are *called* from
# `_gate` itself, well above this point in the file - see the comment there.
# That is a forward reference in reading order, not in execution order:
# nothing calls `_gate` until the server is already answering requests, by
# which time this whole module, including every name below, has finished
# executing. Python resolves a function's globals at call time, not at
# `def` time, so this is the same kind of reference `_gate` already makes to
# names in other modules (`auth.required()`, `workspaces.use()`) that are
# equally not defined at the top of this file.


def _client_write_refused() -> HTTPException:
    """The one answer every refusal on these three routes gives - see
    `_CLIENT_LINK_GONE`'s own docstring. A token that never resolved, an
    intake that has since vanished, a move `intakes.advance` will not make
    from the state the record is actually in: all of them come back exactly
    like this, because a stranger holding a wrong-state link must learn
    nothing more than "no"."""
    return HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)


def _client_advance(intake_id: str, to: str, **fields) -> intakes.Intake:
    """Move a client-writable intake, or answer this door's one refusal.

    Deliberately not preceded by this handler's own `entry.state != ...`
    check: `intakes.advance` already makes that check, atomically, under its
    own lock - a second copy here would be exactly the kind of duplicated
    invariant that let `close()` and `advance(..., CLOSED)` drift apart in
    Task 1 (see `_write`'s own docstring). Relying on it here means a
    double-submit racing itself is refused by the same mechanism that refuses
    a submit from the wrong state, not a second one that could disagree with
    it.

    `intakes.advance` raises `IntakeError` for two entirely different
    reasons: "that move is not legal" (unknown id, wrong state, an unknown
    field) and "the write itself failed" (`_write`'s own wrapped `OSError` -
    a full disk, a sync client holding a lock) - the latter is
    `intakes.IntakeWriteError`, a dedicated subtype precisely so this
    function does not have to tell them apart by reading the exception's own
    message. Those are not the same answer. The first is a refusal, and
    every refusal on this door is the identical opaque 404. The second is
    not a refusal at all - it is this server failing to save a client's
    words - and folding it into "that link is gone" would tell somebody
    their submission was rejected when it was actually lost. Caught first,
    and ahead of the plain `IntakeError` below, since it is a subtype of it -
    listing them in the other order would let the broader `except` swallow
    it before this one ever ran. Deliberately not a pre-read of
    `entry.state` here to classify the failure instead: that would
    reintroduce the duplicated-check problem this function exists to avoid,
    for a distinction `intakes.py` can already make structurally.
    """
    try:
        return intakes.advance(intake_id, to, **fields)
    except intakes.IntakeWriteError as exc:
        deps.logger.exception("Could not save a client write to intake %s (-> %s)", intake_id, to)
        raise HTTPException(
            status_code=500,
            detail="That could not be saved. Wait a moment and try again.",
        ) from exc
    except intakes.IntakeError:
        raise _client_write_refused() from None


def _normalise_client_kind(raw: str) -> str:
    """One of `kinds.BY_ID`, or empty for anything else.

    Membership, never a cast, and never trusted from the wire: this arrives
    from a stranger holding a link, and an id outside the set would reach
    `kinds.resolve` - which falls back to the default - and be stored on the
    record as a discipline nobody offers. Empty is the honest answer for "they
    did not choose", and `App.tsx`'s `readPreset` treats it exactly as it
    treats an absent one: the studio's own preset kind stands.
    """
    return (raw or "").strip().lower() if (raw or "").strip().lower() in kinds.BY_ID else ""


def _normalise_client_kind_label(raw: str) -> str:
    """The client's own word for an `other` discipline, bounded at
    `kinds.MAX_LABEL`.

    Bounded here as well as in the browser, because the browser's `maxLength`
    is a courtesy to whoever is typing and not a control on whoever is
    posting. The value is *sanitised* far downstream, in `prompts.kind_block`,
    which is where it meets the model - deliberately not here, so there is one
    place that decides what is safe to put in a prompt rather than two that
    have to agree.
    """
    return _CONTROL_CHARS.sub("", raw or "").strip()[: kinds.MAX_LABEL]


# --- What a stranger may attach, which is not what a studio may -------------
#
# `_read_images` and `_read_documents`, above, are the studio's own readers and
# this route deliberately does not call them. Three reasons, and none of them is
# a preference:
#
#   * `_read_images` hands back `(bytes, mime)` and drops the filename on the
#     floor. A manifest entry is `{id, name, kind, bytes, note}` and the name is
#     the only thing in it a studio can recognise the file by.
#   * `_read_documents` hands back an `Attachment` - name, kind, extracted text
#     - and drops the *bytes*. There is nothing left to store.
#   * `MAX_CLIENT_FILES` is one number across both fields. Neither reader can
#     see the other's list, so a cap that spans them cannot be enforced from
#     inside either, and splitting six files into three and three would buy a
#     caller twelve.
#
# What is reused is every idiom they established, because those were right and
# were arrived at the hard way: the loose `List[Union[UploadFile, str]]` typing
# (see `_read_images`' docstring for the two shapes of "no file was chosen" that
# reach a handler), the `BaseUploadFile` filter, refusing on the declared size
# before allocating anything, then reading one byte past the limit so an
# understated size cannot get past it either, and closing every upload in a
# `finally`.


class _ClientFile(NamedTuple):
    """One file a client attached, as far as this door is concerned: what they
    called it, what PRISM decided it is, and the bytes that arrived."""

    #: Already through `intakefiles.clean_name` - the same string that will be
    #: written onto the record, so a refusal and a manifest entry cannot name
    #: the file differently.
    name: str
    #: The canonical content type from `intakefiles.resolve_type` - never the
    #: raw `Content-Type` the browser sent, and never a bare extension.
    kind: str
    data: bytes


#: A macro-enabled workbook, subtracted below and named here so the subtraction
#: reads as a decision rather than an omission.
#:
#: This is the type that shows what deriving a set costs. Everything else in
#: `CONTENT_TYPES` is a document or a picture; this one is a program with a
#: spreadsheet wrapped round it, and it was in the accepted set purely because
#: nobody had to type its name to put it there. `Content-Disposition:
#: attachment` is no defence at all against it - the download *is* the delivery.
#: A stranger with one issued link sends `requirements.xlsm` with an auto-open
#: macro, the queue lists it as an ordinary attachment, a studio member opens it
#: and clicks Enable Content, and it is their machine.
#:
#: A client with a spreadsheet has `.xlsx`, which carries every cell of the same
#: data and cannot carry the macro. Nothing legitimate is lost, and the studio's
#: own authenticated pad is unaffected - `_read_documents` still takes what it
#: always took. That asymmetry is the point: this door is the one with a
#: stranger on the other side of it.
_MACRO_SPREADSHEET = "application/vnd.ms-excel.sheet.macroEnabled.12"

#: Every content type a client's own file may be stored as. Derived from
#: `intakefiles.CONTENT_TYPES` rather than written out again beside it: that
#: table is what the store knows how to keep, hand back and label, and a second
#: copy here would be a thing to forget. `application/octet-stream` is
#: subtracted because it is not a type - it is `intakefiles`' word for "no
#: idea", the answer `resolve_type` gives when neither the declared type nor
#: the extension said anything it recognised. A file arriving as "no idea" is
#: precisely what an anonymous door should not be storing, so on this path the
#: fallback *is* the refusal. And `_MACRO_SPREADSHEET`, for the reason above.
#:
#: The raster half of the set is `intakefiles.INLINE_TYPES`, imported rather
#: than restated for the same reason, and it is the closed allowlist the plan
#: requires: `_read_images` admits anything matching `image/`, which includes
#: `image/svg+xml`, and an SVG is a script document that the studio will later
#: open. The gate is on the **declared** type and structurally cannot be on a
#: resolved-from-filename one, because `resolve_type` refuses to let a suffix
#: resolve into `INLINE_TYPES` at all - see its docstring. So a file only ever
#: reaches the raster set by being declared one of them, and `_looks_like`
#: below is what decides whether the declaration was true.
_CLIENT_TYPES = frozenset(intakefiles.CONTENT_TYPES) - {
    intakefiles.FALLBACK_TYPE,
    _MACRO_SPREADSHEET,
}

#: Which of `attachments.py`'s readers each accepted document's extension names,
#: and which one its content type names - the same map read from both sides, so
#: the two can be compared.
#:
#: Two things depend on this and they are worth separating. The first is that a
#: document's extension has to *agree* with its declared type rather than merely
#: be recognised: `scope.txt` carrying PDF bytes and declaring
#: `application/pdf` resolves to `application/pdf` correctly, but
#: `attachments.read` keys its reader off the name, so the text reader would run
#: over PDF bytes, decode them as mojibake, find "text" and report no problem at
#: all. The manifest would then assert a clean read of a file nothing read, and
#: Task 6 would feed the model the same noise. Comparing readers rather than
#: strings is what catches that while still allowing the ordinary disagreements
#: - a `.md` a browser declared `text/plain` is a text file either way.
#:
#: The second is that this is derived from `_CLIENT_TYPES`, so subtracting the
#: macro type above closes the extension road too: `.xlsm` is no longer a key
#: here, so a macro workbook cannot get in by declaring the plain spreadsheet
#: type either - which matters because the studio downloads a file under the
#: *client's* name, and `budget.xlsm` opens in Excel as a macro workbook whatever
#: this server stored it as.
#:
#: `SUFFIXES[suffix]` is indexed rather than `.get`, so a document type added to
#: `intakefiles.CONTENT_TYPES` that no reader in `attachments.py` knows about
#: fails here, at import, with a `KeyError` naming the suffix. That is the
#: intended failure and not an oversight: the alternative is that the new type
#: is quietly unacceptable on this route for ever, which nobody would notice
#: until a client's file was refused. Import happens on every start and every
#: check script, so whoever added the type finds out immediately.
_DOCUMENT_READER = {
    suffix: attachments_module.SUFFIXES[suffix]
    for kind, suffix in intakefiles.CONTENT_TYPES.items()
    if kind in _CLIENT_TYPES and kind not in intakefiles.INLINE_TYPES
}

#: What each of those types actually begins with: a tuple of `(offset,
#: alternatives)` pairs, all of which must match.
#:
#: This is the check that refuses an `.exe` renamed `.pdf`, and it is here
#: rather than left to `attachments.py` on purpose. Both the filename and the
#: `Content-Type` on a multipart part are chosen by whoever is uploading -
#: neither is evidence of anything - so without this, the only thing standing
#: between a client and a Windows executable stored as `application/pdf` is that
#: the studio has to double-click it. `attachments.read` would notice the file
#: was unopenable, but it reports rather than refuses (its module docstring is
#: explicit, and that rule is right for a studio's own tender pack), and reading
#: its answer back out of a message string is exactly the string-matching
#: `_client_advance` was careful not to do.
#:
#: Deliberately container markers only - the thing that says "this is a PNG at
#: all", not a survey of encoders. It is a door check, not a validator: what it
#: has to stop is a file whose bytes are a different *kind of thing* from what
#: it claims, and anything subtler than that is the reader's problem downstream
#: where it is already handled.
#:
#: The three text types are absent, and that is the whole rule for them: plain
#: text, CSV and Markdown have no signature to check, so there is nothing here
#: that could be true or false about them. What that lets through is an
#: executable renamed `.txt`, which is a file that downloads as `.txt`, opens in
#: a text editor and runs nowhere - the case this table exists for is the one
#: where the extension makes it double-clickable.
#:
#: `_MACRO_SPREADSHEET` is absent too, for a different reason: it is refused at
#: the type gate, so `_looks_like` can never be called with it, and an entry for
#: it would be a line nothing could ever execute. This table covers exactly what
#: this door accepts, not everything `intakefiles` can store.
_SIGNATURES: dict[str, tuple[tuple[int, tuple[bytes, ...]], ...]] = {
    "image/png": ((0, (b"\x89PNG\r\n\x1a\n",)),),
    "image/jpeg": ((0, (b"\xff\xd8\xff",)),),
    "image/gif": ((0, (b"GIF87a", b"GIF89a")),),
    # RIFF container, with the form type four bytes after the length.
    "image/webp": ((0, (b"RIFF",)), (8, (b"WEBP",))),
    # ISO base media, whose box header puts `ftyp` at offset 4. The brand that
    # follows (`heic`, `heix`, `mif1`) is not checked: the brands are a moving
    # list and the container is the part that decides what the file is.
    "image/heic": ((4, (b"ftyp",)),),
    "application/pdf": ((0, (b"%PDF-",)),),
    # A `.docx`/`.xlsx` is a zip, and a real one always begins with a local file
    # header. The empty-archive and spanned-archive markers are not accepted:
    # neither could carry a document, and `attachments.py`'s own bound is what
    # deals with an archive that is real but hostile.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        (0, (b"PK\x03\x04",)),
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ((0, (b"PK\x03\x04",)),),
}


def _looks_like(kind: str, data: bytes) -> bool:
    """Whether these bytes begin the way this content type has to.

    A type with no entry in `_SIGNATURES` has no signature to disagree with and
    is let through - see that table's own comment for which types those are and
    why that is the right answer for them rather than an omission.
    """
    for offset, markers in _SIGNATURES.get(kind, ()):
        if not any(data[offset : offset + len(marker)] == marker for marker in markers):
            return False
    return True


def _suffix_of(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


#: What a file this route will not take is answered with. It names the file,
#: because a client with four attachments needs to know which one to remove, and
#: it names what would be taken instead rather than only what would not - the
#: person reading it is filling in a form, not debugging a mime type.
_WRONG_KIND = (
    "'{name}' is not a file PRISM can take. Attach a PDF, a Word document, a "
    "spreadsheet, a CSV or a text file - or a photo or screenshot as PNG, JPEG, "
    "WebP, GIF or HEIC."
)

#: The macro workbook's own answer, because the general one above would read as
#: wrong to the only person who ever sees it: it says a spreadsheet is fine, and
#: they are holding a spreadsheet. This is also the one refusal in the set with
#: a remedy the client can carry out in one step, so it says what that is
#: instead of listing everything they could have sent instead.
_MACRO_REFUSED = (
    "'{name}' is a macro-enabled workbook, and PRISM does not take those. Save "
    "it as .xlsx and attach that - it keeps every cell, without the program."
)


async def _read_client_files(
    images: List[Union[UploadFile, str]] | None,
    documents: List[Union[UploadFile, str]] | None,
) -> List[_ClientFile]:
    """Everything a client attached, validated, or the 400 that says why not.

    Both fields are walked as one list. Which input a file arrived in is a hint
    from a picker this server did not write and cannot trust - what decides how
    a file is treated is what it resolves to, and the caps are counted across
    the pair because a cap counted per field is two caps.

    Unlike `_read_documents`, this refuses rather than reports. That is not a
    contradiction of `attachments.py`'s rule and the difference is worth being
    precise about: an unreadable file is still *reported* here, on the manifest
    entry's `note`, exactly as that module requires. What is refused is a file
    of a kind this route will not store at all - which is a decision about the
    door, made while there is still a person standing at it to be told.
    """
    # Images before documents, matching this route's own signature and
    # `create_proposal`'s reading order. It has to be *some* order and it cannot
    # be the client's: a browser sends one interleaved sequence of parts and
    # FastAPI has already split it into two lists by the time a handler sees it,
    # so the order somebody picked their files in is gone before this line.
    candidates = [
        upload
        for field in (images, documents)
        for upload in (field or [])
        if isinstance(upload, BaseUploadFile) and (upload.filename or "").strip()
    ]
    if not candidates:
        return []

    if len(candidates) > config.MAX_CLIENT_FILES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(candidates)} files attached. You can send up to "
                f"{config.MAX_CLIENT_FILES} - remove the rest and submit again."
            ),
        )

    files: List[_ClientFile] = []
    total = 0
    limit_mb = config.MAX_CLIENT_FILE_BYTES / (1024 * 1024)
    total_mb = config.MAX_CLIENT_UPLOAD_TOTAL_BYTES / (1024 * 1024)

    for upload in candidates:
        # Cleaned here rather than left to `save()`, and the cleaned string is
        # what everything downstream uses - the checks below, the refusals that
        # name the file, and the manifest entry. `clean_name` caps at 120
        # characters and strips control characters and separators, so a caller
        # cannot put a multi-kilobyte name with a newline in it into a response
        # body by being refused instead of accepted, and the file is called one
        # thing rather than two.
        name = intakefiles.clean_name(upload.filename or "")
        declared = (upload.content_type or "").split(";")[0].strip().lower()
        # The store's own resolution, not a second copy of it: the declared type
        # wins when PRISM knows it, the extension is consulted only when it said
        # nothing, and an extension can never resolve into the raster allowlist.
        kind, _stored_as = intakefiles.resolve_type(declared, name)

        # Two refusals with one message, because they are one thing to the person
        # reading it: PRISM does not take that. The second clause is the
        # document whose extension and declared type do not name the same
        # reader - including the document with no usable extension at all, which
        # `_DOCUMENT_READER.get` answers `None` for. See that map's own comment
        # for what each half of it is holding up.
        if kind not in _CLIENT_TYPES or (
            kind not in intakefiles.INLINE_TYPES
            and _DOCUMENT_READER.get(_suffix_of(name))
            != _DOCUMENT_READER[intakefiles.CONTENT_TYPES[kind]]
        ):
            await upload.close()
            # Both roads into the macro refusal get the message about macros:
            # the type, when a browser declared it, and the extension, when it
            # declared an ordinary spreadsheet or nothing at all. Which of the
            # two clauses above caught it is not something the person reading
            # the sentence should have to know.
            answer = (
                _MACRO_REFUSED
                if kind == _MACRO_SPREADSHEET or _suffix_of(name) == ".xlsm"
                else _WRONG_KIND
            )
            raise HTTPException(status_code=400, detail=answer.format(name=name))

        # Refused on what the part declares before anything is allocated for it,
        # exactly as `_read_images` does and for the reason its own comment
        # gives.
        declared_size = getattr(upload, "size", None)
        if declared_size is not None and declared_size > config.MAX_CLIENT_FILE_BYTES:
            await upload.close()
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is {declared_size / (1024 * 1024):.1f} MB. The limit is "
                    f"{limit_mb:.0f} MB per file."
                ),
            )

        try:
            # One byte past the limit is all the check below needs, and a part
            # that understated its own size does not get past it either.
            data = await upload.read(config.MAX_CLIENT_FILE_BYTES + 1)
        except Exception as exc:  # noqa: BLE001 - whatever the spool did, the client hears one thing
            deps.logger.warning("Could not read the client's upload %s: %s", name, exc)
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' could not be read. Attach it again and submit.",
            ) from exc
        finally:
            await upload.close()

        if not data:
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is empty. Attach it again and submit.",
            )
        if len(data) > config.MAX_CLIENT_FILE_BYTES:
            # The read stopped one byte over, so the real size is not known here
            # - say so rather than quote the cut-off as if it were a measurement.
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is larger than the {limit_mb:.0f} MB limit per file.",
            )

        # The aggregate, measured on what actually arrived. `_gate` already
        # bounded the *body* against this same number, and this is deliberately
        # not a duplicate of that check: the body is the envelope and this is
        # what is inside it, so a caller cannot get past the second by arranging
        # the first. See `config.MAX_CLIENT_UPLOAD_TOTAL_BYTES`'s own comment.
        total += len(data)
        if total > config.MAX_CLIENT_UPLOAD_TOTAL_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Those files come to more than {total_mb:.0f} MB together. Send fewer, "
                    "or smaller ones."
                ),
            )

        if not _looks_like(kind, data):
            raise HTTPException(status_code=400, detail=_WRONG_KIND.format(name=name))

        files.append(_ClientFile(name, kind, data))

    return files


def _store_client_files(intake_id: str, files: List[_ClientFile]) -> List[dict]:
    """Extract, save, manifest - in that order, and all of it off the loop.

    Every line of this is blocking, which is why it is a plain `def` called
    through `asyncio.to_thread` (the idiom this file already uses for
    `mailer.send_invite`) rather than inlined into an `async def`.
    `attachments.read` opens a PDF or unpacks a zip, which is CPU-bound and can
    be seconds on a large one; `intakefiles.save` is a network round trip per
    file once Spaces is configured, and six files is six of them. Held on the
    event loop that would park every other request this worker has, including
    another client's `/submit`.

    One hop for the whole batch rather than one per file: the borrow is a
    `ContextVar` and `to_thread` runs the call in a copy of the current context,
    so the workspace this was borrowed under is what the thread saves into.

    Raises `intakefiles.IntakeFileError` - the caller turns it into a 500,
    because a client whose file did not reach the bucket has had their
    submission lost, not refused.
    """
    manifest: List[dict] = []
    for entry in files:
        # The extraction warning, and only for the kinds there is one for.
        # `attachments.read` keys on the suffix and would answer "not a file
        # type PRISM can read" for a photograph, which is true and useless: an
        # image reaches the model as an image, and nothing was ever going to
        # read text out of it.
        note = (
            ""
            if entry.kind in intakefiles.INLINE_TYPES
            else attachments_module.read(entry.name, entry.data).problem
        )
        manifest.append(
            intakefiles.save(intake_id, entry.name, entry.data, entry.kind, note=note)
        )
    return manifest


class ClientReviseRequest(BaseModel):
    """What the client asked to change, in their own words."""

    asked: str = ""


@router.post("/api/client/{token}/submit", response_model=None, tags=["client"])
async def submit_client_intake(
    token: str,
    client_email: str = Form(""),
    client_phone: str = Form(""),
    scope: str = Form(""),
    budget_text: str = Form(""),
    #: Which discipline the client says this is, and - for `other` alone -
    #: their own word for it. Plain `str` for the same reason `Intake.state`
    #: is: the closed set lives in `kinds.BY_ID` and is enforced by
    #: `_normalise_client_kind`, not on the way in, so an unknown id is
    #: answered as the empty string rather than as a 422 naming every kind
    #: this build happens to know.
    client_kind: str = Form(""),
    client_kind_label: str = Form(""),
    #: Typed as loosely as the studio's own upload routes, and for the reason
    #: `_read_images`' docstring gives: a browser posts an empty file input as
    #: a part with no filename, and a strict `List[UploadFile]` makes FastAPI
    #: refuse the whole request with an unreadable 422 before any handler runs
    #: - which on this route would be a client with nothing attached being told
    #: their form is malformed.
    images: List[Union[UploadFile, str]] = File(default=[]),
    documents: List[Union[UploadFile, str]] = File(default=[]),
) -> dict:
    """The client's own words and their own files, written once, from `issued`.

    Multipart rather than JSON since Task 3: the four fields are `Form`, and the
    two file lists are the studio's own field names so one picker's split - what
    goes to the model as an image, what goes as text - reads the same on both
    sides. Which of the two a file arrived in is only a hint, though; see
    `_read_client_files`.

    There is no studio identity behind this call - just whoever is holding the
    link - so `intakes.advance`'s transition table is the entire abuse control:
    a second call, from `submitted` or anywhere past it, is refused exactly as a
    call against a token that never resolved is. All four text fields are
    bounded: `_normalise_scope`/`_normalise_budget_text` are the same the
    studio's own `/api/intakes` route uses, and `_normalise_client_email`/
    `_normalise_client_phone` apply the identical idiom to the two fields that
    used to get only a bare `.strip()` - see their own docstrings.

    No `Request` parameter, and no `_enforce_rate_limit` call here - unlike the
    first cut of this route. `_gate` (`app/presentation/api/middleware.py`) now makes
    that check itself, ahead of routing, so it runs before FastAPI reads or
    parses this request's body at all rather than after. See the comment on that
    clause for why a check placed here, inside the handler, can never be truly
    first.
    """
    found = tokens.resolve(token)
    if found is None:
        raise _client_write_refused()

    # The words first, and the files after. Deliberately in that order: an
    # over-length scope is refused by a string comparison, and doing it before a
    # single byte is copied out of the multipart spool means the commonest
    # refusal costs nothing and - the part that matters - cannot leave anything
    # behind in storage.
    said_email = _normalise_client_email(client_email)
    said_phone = _normalise_client_phone(client_phone)
    said_scope = _normalise_scope(scope)
    said_budget = _normalise_budget_text(budget_text)
    said_kind = _normalise_client_kind(client_kind)
    # The same question the studio's own pad is asked, on the same door the
    # structural floor above guards - because the floor cannot reach the case
    # that made this necessary. "erwerasdad dklajdlaksdjacsdasd" is thirty
    # characters, thirteen distinct letters and two words; only reading it
    # tells it from a scope.
    #
    # HERE, rather than inside a job, because there is no job: `/submit` is the
    # client's whole interaction and it answers synchronously. The call runs in
    # a worker thread (`anyio.to_thread` inside `check_brief_is_real`), so the
    # event loop is not parked while it waits.
    #
    # BEFORE `_read_client_files` below, which is what makes a refusal free: no
    # part is parsed, no bytes are held, nothing reaches Spaces, and - the half
    # that matters - `advance()` is never called, so the intake stays `issued`
    # and the client's one write is NOT spent. That last point is why this is
    # safe to put on an anonymous door at all. A refused client retypes and
    # sends again on the same link; they do not lose it. Every case in
    # check_client_api.py asserts exactly that, for this refusal as for the
    # structural ones.
    #
    # Fails open like every other caller of this function - see its docstring.
    # A studio would rather read one nonsense enquiry than have a model outage
    # quietly stop their clients submitting anything at all.
    if config.CHECK_BRIEF_IS_REAL:
        verdict = await check_brief_is_real(said_scope)
        if not verdict.is_brief:
            raise HTTPException(
                status_code=400,
                detail=(
                    verdict.reason
                    or "That does not read as a description of work. Say what you need, "
                    "who it is for, and anything that matters about how it should work."
                ),
            )
    # Read for `other` alone, exactly as `prompts.kind_block` reads it: every
    # other kind carries its own name already, so a label sent alongside one
    # of them is a word nothing will ever use, and storing it would put a
    # discipline on the record that the studio would reasonably believe was
    # chosen. Dropped rather than stored-and-ignored.
    said_label = (
        _normalise_client_kind_label(client_kind_label) if said_kind == kinds.OTHER.id else ""
    )

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        files = await _read_client_files(images, documents)
        manifest: List[dict] = []

        if files:
            # An orphan filter, and explicitly **not** a second authority on
            # whether this write may happen. `_client_advance`'s own docstring
            # says why a duplicated state check is a bad idea and it still
            # holds: `intakes.advance` makes that decision atomically under its
            # own lock, this does not, and the refusal below is
            # `_client_write_refused()` - the identical opaque 404 - precisely
            # so no caller can tell which of the two answered.
            #
            # What it buys is the thing storage made new. Bytes are written
            # before the record can be moved (see below for why that order and
            # not the other), so a submit that was always going to be refused -
            # a used link, a closed intake, a second click - would otherwise put
            # up to six files in a bucket with nothing on any record pointing at
            # them, on an anonymous route, as often as they cared to try.
            # Reading the state first turns the *sequential* case of that into
            # no write at all, which is the case that actually happens: a client
            # who clicks twice, a browser that retries, a link forwarded to
            # somebody who tries it after the fact.
            #
            # What it does not bound is concurrency, and the arithmetic is worth
            # writing down rather than being reassured by. There is an `await`
            # between this read and the advance below - the `to_thread` that
            # does the saving - so every request in flight at once passes this
            # check before any of them reaches `advance()`. Twenty simultaneous
            # submits against one live link, which the per-address limiter
            # permits in a minute, is nineteen losers times six files times six
            # megabytes: on the order of 700 MB stored with nothing pointing at
            # it and no per-file delete to take it back. This check is an orphan
            # *filter*, not a bound, and nothing here is a defence against a
            # caller who is trying - see `_rate_limit_hits`' own comment for the
            # same disclaimer about the only other control on this door.
            #
            # Closing the window would mean claiming the intake atomically
            # before the files exist, and the only thing in this codebase that
            # can claim one is `advance()` itself - which is the manifest-first
            # ordering rejected below, for a worse reason than this one.
            entry = intakes.get(intake_id)
            if entry is None or entry.state != intakes.ISSUED:
                raise _client_write_refused()

            try:
                manifest = await asyncio.to_thread(_store_client_files, intake_id, files)
            except intakefiles.IntakeFileError as exc:
                # Not this door's opaque 404. Their submission was lost, not
                # refused, and the two must not read the same - the same
                # distinction `_client_advance` draws for a failed `_write`.
                #
                # A batch that failed halfway leaves whatever landed before it
                # did, and those are orphans too. They are not re-listed here
                # because `intakefiles.save` logs every file it stores, by id
                # and by intake, so the log already has them - and this
                # function never learns their ids, since the exception is what
                # came back instead of the manifest.
                deps.logger.exception("Could not store a client's files for intake %s", intake_id)
                raise HTTPException(
                    status_code=500,
                    detail="That could not be saved. Wait a moment and try again.",
                ) from exc

        try:
            moved = _client_advance(
                intake_id,
                intakes.SUBMITTED,
                client_email=said_email,
                client_phone=said_phone,
                scope=said_scope,
                budget_text=said_budget,
                client_kind=said_kind,
                client_kind_label=said_label,
                attachments=manifest,
                #: The one moment nothing else on the record captures. Every
                #: other timestamp here belongs to the studio - when the link
                #: was minted, sent, closed - and this is the client's.
                submitted_at=storage.utc_now_iso(),
            )
        except HTTPException:
            # Save, then advance - and this is the failure that ordering leaves.
            # The other order is worse: a manifest written first would point at
            # files that then failed to arrive, on a record that can never be
            # re-submitted, and the client would be told everything went fine.
            # An orphan is bytes nobody is looking at; the alternative is a
            # record that lies.
            #
            # Nothing is deleted here, and that is deliberate rather than
            # unfinished. `intakefiles.forget()` is the only removal the store
            # offers and it deletes the intake's **whole prefix** - so calling
            # it on the path that actually reaches this line, which is
            # overwhelmingly "somebody else already submitted", would delete the
            # legitimate submission's files. Cleaning up would need a per-file
            # delete that Task 2's interface does not have; until it does, the
            # ids are logged so a bucket audit has something to match against.
            if manifest:
                deps.logger.error(
                    "Orphaned %d stored file(s) for intake %s - the submission was refused "
                    "after they were saved: %s",
                    len(manifest),
                    intake_id,
                    ", ".join(entry["id"] for entry in manifest),
                )
            raise

        # `submitted` is one of `clientview.of`'s waiting states, which needs
        # no bundle - passing none is correct, exactly as it is for `issued`.
        return clientview.of(moved)
    finally:
        workspaces.give_back(borrowed)


@router.post("/api/client/{token}/revise", response_model=None, tags=["client"])
async def revise_client_intake(token: str, body: ClientReviseRequest) -> dict:
    """Ask for a change. Accepted only from `sent`, and only once at a time -
    a second ask before the studio has re-quoted moves the record to
    `revision_requested`, which is not `sent`, so `intakes.advance` refuses
    it the same as any other wrong-state call.

    `revisions` is a log `advance()` overwrites wholesale rather than appends
    to (see `ADVANCE_FIELDS`'s own docstring), so the full, updated list is
    built here from the record's current one and handed in whole.

    No `Request` parameter and no `_enforce_rate_limit` call - see
    `submit_client_intake`'s docstring for why: `_gate` makes this check now,
    ahead of routing and body parsing.
    """
    found = tokens.resolve(token)
    if found is None:
        raise _client_write_refused()

    asked = deps.normalise_instruction(body.asked)
    if not asked:
        raise HTTPException(
            status_code=400,
            detail="Say what you would like changed before asking for a revision.",
        )

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        # A best-effort read, not a check: if the intake has vanished or
        # moved on since this line, `existing` may be stale, but
        # `_client_advance` below re-reads the record fresh, inside
        # `advance`'s own lock, and refuses correctly off that read
        # regardless of what this one saw.
        current = intakes.get(intake_id)
        existing = list(current.revisions) if current is not None else []
        moved = _client_advance(
            intake_id,
            intakes.REVISION_REQUESTED,
            revisions=existing + [{"asked": asked, "at": storage.utc_now_iso()}],
        )
        bundle = storage.get(moved.sent_bundle_id) if moved.sent_bundle_id else None
        try:
            return clientview.of(moved, bundle)
        except ValueError:
            # `clientview.of` raises for a quoted-face state with no bundle
            # attached (see its own docstring) - reachable here because
            # nothing in `intakes.py` keeps `sent_bundle_id` pointing at a
            # real bundle: `DELETE /api/proposals/{id}` can remove one out
            # from under a `sent` intake today, and `QUOTED -> SENT` never
            # required the reverse. `moved` is already persisted at this
            # point - the transition itself cannot be undone - so this
            # answers the same opaque refusal `read_client_view` gives for
            # the identical failure, rather than a 500 with a traceback.
            deps.logger.exception(
                "clientview.of could not show intake %s after revise - dangling bundle?",
                intake_id,
            )
            raise _client_write_refused() from None
    finally:
        workspaces.give_back(borrowed)


@router.post("/api/client/{token}/finalize", response_model=None, tags=["client"])
async def finalize_client_intake(token: str) -> dict:
    """The client accepts what was sent. Not a signature and nothing is
    charged - that framing lives on the client's own screen (Task 8), not
    here. Accepted only from `sent`. Tells the intake's own author and every
    admin, by an explicit recipient list rather than a role alone, since
    whoever issued this particular request should hear about it even if they
    do not administer the workspace.

    No `Request` parameter and no `_enforce_rate_limit` call - see
    `submit_client_intake`'s docstring for why: `_gate` makes this check now,
    ahead of routing.
    """
    found = tokens.resolve(token)
    if found is None:
        raise _client_write_refused()

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        moved = _client_advance(intake_id, intakes.FINALIZED)
        bundle = storage.get(moved.sent_bundle_id) if moved.sent_bundle_id else None

        # Built and validated *before* the notification below runs, on
        # purpose. `moved` is already persisted at this point - the
        # transition itself cannot be undone - but nothing external has been
        # told anything yet. `clientview.of` raises `ValueError` for a
        # quoted-face state with no bundle attached (see its own docstring),
        # reachable here for the same reason `/revise` guards it above:
        # nothing in `intakes.py` keeps `sent_bundle_id` pointing at a real
        # bundle, and `DELETE /api/proposals/{id}` can remove one out from
        # under a `sent` intake today. Failing *here*, ahead of
        # `inbox.notify`, is the point: the worst case is a client seeing
        # this door's ordinary opaque refusal on a state that quietly did
        # move - not the studio being told a quotation was accepted while
        # the client who "accepted" it stares at an error. Calling
        # `clientview.of` bare, or guarding it only after notifying, would
        # produce exactly that inversion.
        try:
            view = clientview.of(moved, bundle)
        except ValueError:
            deps.logger.exception(
                "clientview.of could not show intake %s after finalize - dangling bundle?",
                intake_id,
            )
            raise _client_write_refused() from None

        # Resolved against the roster at write time, the same as every other
        # notification in this file - see `inbox.py`'s own docstring on why.
        # A plain union of two sets rather than two separate `notify()`
        # calls, so an admin who also happens to be `created_by` is told
        # once, not twice.
        roster = members.listing()
        roster_emails = {(member.email or "").strip().lower() for member in roster}
        admin_emails = {
            (member.email or "").strip().lower()
            for member in roster
            if member.role == members.ADMIN
        }
        recipients = set(admin_emails)
        created_by = (moved.created_by or "").strip().lower()
        if created_by and created_by in roster_emails:
            recipients.add(created_by)

        words = {
            "title": "A client finalised their quotation",
            "body": " · ".join(
                part
                for part in (
                    bundle.estimate.quotation_ref if bundle else "",
                    bundle.estimate.client_name if bundle else "",
                    format_money(bundle.estimate.cost.total, bundle.estimate.currency)
                    if bundle
                    else "",
                )
                if part
            ),
            "href": "#/intakes",
        }
        if recipients:
            inbox.notify("intake.finalized", list(recipients), words)
        if created_by and created_by not in roster_emails:
            # `created_by` is no longer on this workspace's team.
            # `inbox.notify`'s list-audience path (`inbox._people`) only
            # ever matches names against the *current* roster, so a former
            # member would otherwise hear nothing about a request they
            # personally took in - silently breaking this handler's own
            # promise, stated above, that the author is told "even if they
            # do not administer the workspace" (a departed member does not
            # administer it either, and this is the sharper case of the
            # same promise). `inbox.deliver` writes straight to a named
            # person's file with no roster check at all, which is exactly
            # what is needed here - and cannot double-notify: this branch
            # and the `recipients`-based call above are mutually exclusive
            # by construction, since `created_by in roster_emails` decides
            # which one this person can ever reach.
            inbox.deliver(inbox.key_for(created_by), "intake.finalized", words)

        return view
    finally:
        workspaces.give_back(borrowed)


# --- The client reads the quotation (Stage 2 Task 5) ---------------------------
#
# One more door on the same anonymous prefix as Tasks 3 and 4: no
# `Authorization` header, resolved by the token alone. PRISM writes two
# documents from every estimate it prices - the one a client is meant to
# read, and the studio's own internal one beside it, with the stack, the
# API surface, the phases and the open questions written for whoever builds
# the thing, not whoever is buying it. Every file route above this section
# (`download_markdown`, `printable_html`, `download_pdf`) takes which of the
# two to serve as a `{kind}` path segment, because the caller there is a
# signed-in studio member choosing on purpose. A client did not choose
# anything - they were sent one link - and this door must not let them turn
# that link into a way to read the other document. See `_client_quotation`'s
# own docstring for how that is enforced, not merely asserted.

#: The only three states in which a client may read the quotation they were
#: sent. Deliberately not `clientview._QUOTED_FACE`, reused: that set
#: governs what `clientview.of` renders a bundle *for*, and tying this
#: door's own refusal to it would mean a state added there for a reason
#: that has nothing to do with reading a document silently widens what this
#: route serves too. An allow-list, not the denylist `read_client_view`'s
#: own `CLOSED` check is - that route shows *something* for every state but
#: one; this route shows a document for three of the ten states
#: `intakes.ALLOWED` names, so `closed` needs no check of its own here: it
#: was simply never on the list, the identical answer every other unlisted
#: state gets. One of those other seven, `intakes.PROPOSAL_SENT`, is left
#: off on purpose rather than by oversight: nothing advances an intake to it
#: until Stage 3 builds the actor that does (see its own comment in
#: `intakes.py`), and whether a client may still open their quotation once
#: the proposal itself has gone out is a call Stage 3 should make on
#: purpose, not inherit as a 404 nobody actually decided.
_CLIENT_QUOTATION_STATES = {intakes.SENT, intakes.REVISION_REQUESTED, intakes.FINALIZED}


def _client_quotation(token: str, render: Callable[[str, Estimate, int], Response]) -> Response:
    """Resolve a client's own link, refuse everything this door will not
    discuss, and hand back whatever `render` builds from the one document a
    client may ever read here.

    There is no name anywhere in this function - or in either route below
    that calls it - for the document this door refuses to serve: not a
    parameter, not a variable, not a comment. What document gets built is a
    single local constant, set once, on the next line. A value reachable
    from anything a request carries is the one bug this task exists to make
    structurally impossible, not merely rejected after the fact - and the
    surest way to prove nothing reads it off the request is to never spell
    it out at all.

    Everything happens before `give_back` runs, `render` included - the same
    discipline `read_client_view` follows, and for the same reason: nothing
    here may run against whatever workspace `_gate` left the ambient context
    pointed at from this request's own `X-Workspace` header (irrelevant on
    this door - see that route's docstring), only the workspace the token
    itself named.

    Refused with this door's one opaque body, exactly as every other route
    on this prefix refuses: an unknown, expired or relinked-away token
    (`tokens.resolve`); a real token whose intake has moved to a state
    outside `_CLIENT_QUOTATION_STATES`; and a sent-family intake whose
    bundle has since been deleted, or never carried this document at all -
    the same dangling-bundle case `/revise` and `/finalize` above already
    guard, reached here through a different door.
    """
    kind = "proposal"

    found = tokens.resolve(token)
    if found is None:
        raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

    workspace_id, intake_id = found
    borrowed = workspaces.borrow(workspace_id)
    try:
        entry = intakes.get(intake_id)
        if entry is None or entry.state not in _CLIENT_QUOTATION_STATES:
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

        bundle = storage.get(entry.sent_bundle_id) if entry.sent_bundle_id else None
        if bundle is None:
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

        markdown = storage.markdown_for(bundle, kind)
        if markdown is None:
            raise HTTPException(status_code=404, detail=_CLIENT_LINK_GONE)

        return render(markdown, bundle.estimate, bundle.revision)
    finally:
        workspaces.give_back(borrowed)


@router.get("/api/client/{token}/quotation.html", tags=["client"])
async def client_quotation_html(token: str) -> HTMLResponse:
    """Print-ready HTML of the document a client is allowed to read.

    No `kind` anywhere - see `_client_quotation`'s own docstring."""
    kind = "proposal"

    def render(markdown: str, estimate: Estimate, revision: int) -> HTMLResponse:
        # Loaded here, inside the borrow `_client_quotation` is still
        # holding while `render` runs - the studio the token itself names,
        # never whatever `_gate` left ambient from this request's own
        # (irrelevant on this door) `X-Workspace` header. Without this, the
        # page a client is asked to read carries "PRISM" - the tool's name -
        # on its letterhead instead of the studio's, and none of a studio's
        # saved colours, fonts or logo. `render_print_html`'s own docstring
        # is explicit that `brand`/`design` exist for exactly this: a
        # document a client reads, not one a studio member is looking at on
        # their own screen (which is what every *other* caller of this
        # renderer in this file is - `printable_html`, on the studio's own
        # authenticated side of the app).
        studio = settings.load()
        try:
            html = render_print_html(
                markdown,
                deps.document_title(estimate, kind),
                estimate,
                kind=kind,
                brand=studio.studio_name,
                design=studio.proposal_design,
            )
        except Exception as exc:  # pragma: no cover - a renderer bug, not user input
            deps.logger.exception("HTML rendering failed for a client's own quotation")
            raise HTTPException(
                status_code=500,
                detail="The page could not be produced. Try again shortly.",
            ) from exc
        return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

    return _client_quotation(token, render)


@router.get("/api/client/{token}/quotation.pdf", tags=["client"])
def client_quotation_pdf(token: str) -> Response:
    """The same document as a PDF, for whoever would rather attach it to an
    email than open a link. No `kind` anywhere - see `_client_quotation`'s
    own docstring.

    A plain `def`, not `async def` like its `.html` sibling above: building
    this PDF is real CPU work - reportlab's layout pass, not I/O - and this
    door is anonymous with no rate limit of its own (`_gate` only gates the
    three POST routes; a GET here gets neither the limiter nor the body
    cap). Run on the event loop, that cost is paid by every other request
    this worker is holding, not just this one. A synchronous path operation
    is what tells FastAPI to run it on its threadpool instead.
    `download_pdf` (the studio's own file route, same reportlab cost) is
    still `async def` and still pays this - not fixed here, since it sits
    behind auth and is out of this task's scope, but named so the gap is
    not mistaken for solved elsewhere in this file. This route has no such
    gate, which is exactly why leaving it `async def` is the sharper
    version of the same problem.
    """
    kind = "proposal"

    def render(markdown: str, estimate: Estimate, revision: int) -> Response:
        # See `client_quotation_html`'s own `render` for why this is loaded
        # here rather than left at the renderer's bare defaults.
        # `render_pdf` has no separate `brand` text parameter the way
        # `render_print_html` does - a studio's mark reaches the PDF only
        # through `design.logo`, and absent one, the page falls back to the
        # document's own label ("Quotation"), never to the tool's name.
        studio = settings.load()
        try:
            data = render_pdf(
                markdown,
                deps.document_title(estimate, kind),
                estimate,
                kind=kind,
                design=studio.proposal_design,
            )
        except Exception as exc:  # pragma: no cover - a renderer bug, not user input
            deps.logger.exception("PDF rendering failed for a client's own quotation")
            raise HTTPException(
                status_code=500,
                detail="The PDF could not be produced. Try again shortly.",
            ) from exc

        filename = deps.download_filename(estimate, kind, revision).removesuffix(".md") + ".pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    return _client_quotation(token, render)
