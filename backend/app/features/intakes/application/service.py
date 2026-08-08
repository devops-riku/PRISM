"""A client request, from the words they said to the quotation it became.

PRISM's two stored records are snapshots on purpose: a `ProposalBundle` is what
was quoted and a `ProposalDocument` is what was sent, and neither is edited
after the fact - rebuilding produces a new one with a new id. That is right for
documents and useless for a conversation, which has a state that changes.

So this is the third kind of record, and the only one that moves: what a client
asked for, where that request has got to, and which bundles came out of it. It
is storage-side and never reaches the model, so it lives here rather than in
`schemas.py`, exactly as `members.Invite` does.

Each intake is stored as a workspace-scoped SQL aggregate. Uploaded bytes stay
outside SQL under `_intake_files/` or in DigitalOcean Spaces; the intake keeps
only their manifest.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import List

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.features.intakes.infrastructure import files as intakefiles
from app.features.intakes.infrastructure import tokens
from app.features.quotations.infrastructure import repository as storage
from app.features.workspaces.infrastructure import repository as workspaces
from app.shared.infrastructure import database

logger = logging.getLogger("prism.intakes")

DIRNAME = "_intakes"
RECORD_KIND = "intake"

#: How long a client's link is good for. Longer than `members.INVITE_DAYS`
#: (14 days, for a teammate accepting a place already offered to them) on
#: purpose: a client deciding whether to commission a studio at all is on a
#: slower clock, and a link that expired mid-decision would read as the
#: studio changing its mind rather than as a courtesy about stale mail.
LIFETIME_DAYS = 60

#: The lifecycle, in the order a request normally moves through it. Stage 1
#: built the machine and reached `submitted` through `quote_failed`; every
#: name below has existed since Stage 1 (see `ALLOWED`'s history) but
#: `issued`, `sent`, `revision_requested` and `finalized` were refused until
#: Stage 2 opened them.
ISSUED = "issued"
SUBMITTED = "submitted"
PREPARING = "preparing"
QUOTED = "quoted"
QUOTE_FAILED = "quote_failed"
SENT = "sent"
REVISION_REQUESTED = "revision_requested"
FINALIZED = "finalized"
#: Written since Stage 1, present in the table below, and still not
#: reachable: nothing calls `advance(..., PROPOSAL_SENT)` until Stage 3
#: builds the actor that sends a finalized proposal onward.
PROPOSAL_SENT = "proposal_sent"
CLOSED = "closed"

#: What may follow what. A move not listed here is refused, which is what makes
#: this a state machine rather than a string field somebody assigns to.
#:
#: `QUOTED: {PREPARING, ...}` - a second Generate is reachable in the shipped
#: UI: Create PAD -> the pad -> Generate -> `#/q/<bundle>` -> browser Back ->
#: the pad again, still prefilled with the same `intake_id` -> Generate again.
#: Refusing that move (as this table used to) does not stop the second
#: quotation from being made - the pad has no idea the intake is already
#: `quoted` - it only stops the intake from recording it, so the record keeps
#: pointing at the first bundle while the studio is looking at a second,
#: different one on screen. That is the exact pairing this feature exists to
#: produce, made silently wrong instead of merely absent.
#:
#: So the second pass REPLACES. `bundle_ids`, `priced_scope` and
#: `priced_budget` are overwritten with whatever the new pass produced, the
#: same way `priced_scope` was already a snapshot rather than a log. The
#: record answers "what is this request currently quoted at", not "everything
#: it has ever been quoted at" - a Stage 2 reader deciding which bundle the
#: client sees needs the latest, not a history, and a history is what
#: appending would have built by accident. `REVISION_REQUESTED: {PREPARING,
#: ...}` follows the same rule for the same reason: a re-quote after a client
#: asks for a change replaces the quotation exactly as a second Generate
#: always has. `revisions` (below) is the log `bundle_ids` deliberately is
#: not - every round the client asks for is appended there, never
#: overwritten, because "what has this client asked for, in order" is a
#: history and pretending otherwise is what would lose it.
ALLOWED: dict = {
    ISSUED: {SUBMITTED, CLOSED},
    SUBMITTED: {PREPARING, CLOSED},
    PREPARING: {QUOTED, QUOTE_FAILED, CLOSED},
    QUOTED: {PREPARING, SENT, CLOSED},
    QUOTE_FAILED: {PREPARING, CLOSED},
    SENT: {REVISION_REQUESTED, FINALIZED, CLOSED},
    REVISION_REQUESTED: {PREPARING, CLOSED},
    FINALIZED: {PROPOSAL_SENT, CLOSED},
    PROPOSAL_SENT: {CLOSED},
    CLOSED: set(),
}

#: The only fields a transition may write. `id`, `state`, `created_at` and every
#: other bookkeeping field are deliberately absent, so a caller can never fork a
#: record onto a new id or overwrite a pydantic method by passing its name as a
#: keyword - both were reachable through the `hasattr` check this replaces.
#:
#: `revisions`, `sent_bundle_id` and `sent_at` are Stage 2's additions: the
#: first is the log `REVISION_REQUESTED` appends to, the second is which
#: bundle among `bundle_ids` a `send` call actually sent - named explicitly
#: rather than assumed, since a re-quoted intake can have more than one
#: candidate on file - and the third is when that call happened, which
#: `app.clientview` needs to tell a client when their quotation was sent and
#: has nowhere else to read it from: `bundle.created_at` is when the
#: quotation was *prepared*, not when the studio actually let the client see
#: it, and those two moments can be days apart.
#:
#: `client_email`, `client_phone`, `scope` and `budget_text` are Task 4's
#: addition, for the same reason every other name here exists: `app/presentation/api/client.py`'s
#: `/api/client/{token}/submit` has to write the client's own four fields the
#: moment `issued` becomes `submitted`, and nothing before Task 4 ever wrote
#: them anywhere but `create()`. This set is **not** scoped per transition -
#: `advance()` has no notion of "this field, only on that move" - so adding
#: these four here technically lets *any* call to `advance()` overwrite a
#: client's own words, not only the `issued -> submitted` one `/submit` makes.
#: What actually keeps that from happening is that `/submit` is the only
#: caller in this codebase that ever passes them; `check_client_api.py` has a
#: regression test asserting `/revise` and `/finalize` leave them untouched.
#: Narrowing `ADVANCE_FIELDS` itself to be transition-aware would close this
#: gap structurally rather than by convention, and is worth doing the day a
#: second write path needs a field the first should not be able to touch.
#:
#: `attachments` is here for exactly that reason and inherits exactly that
#: caveat: `/submit` writes the manifest in the same `issued -> submitted` move
#: it writes the client's four fields in, so the name has to be allowed, and
#: nothing scopes it to that one move. What keeps it honest is the same thing:
#: `/submit` is its only caller, and `advance()` revalidates the whole record
#: through `Intake.model_validate` before anything is written, so a malformed
#: manifest is refused at the write rather than discovered at the read.
ADVANCE_FIELDS = {
    "job_id",
    "bundle_ids",
    "document_id",
    "priced_scope",
    "priced_budget",
    "error",
    "revisions",
    "sent_bundle_id",
    "sent_at",
    "client_email",
    "client_phone",
    "scope",
    "budget_text",
    "client_kind",
    "client_kind_label",
    "attachments",
    "submitted_at",
}


class IntakeError(Exception):
    """A move the machine does not allow, or an intake that is not there."""


class IntakeWriteError(IntakeError):
    """`_write` could not persist the record - a full disk, a sync client
    holding a lock - as distinct from every other `IntakeError`, which means
    "that move is not legal". A caller that needs to tell "refused" from
    "lost" apart (`app/presentation/api/client.py`'s `_client_advance`, for the client's own write
    routes: a refusal is this door's one opaque 404, but a save failure must
    never read as a dead link - that would tell somebody their submission
    was rejected when it was actually lost) catches this subtype first.
    Kept a subtype of `IntakeError` rather than a separate hierarchy so
    every existing `except IntakeError` in this codebase still catches it
    unchanged."""


class Intake(BaseModel):
    """One client request and everything that has happened to it."""

    id: str = ""
    state: str = ISSUED
    created_at: str = ""
    created_by: str = ""

    #: The client's own link, as a bare token - `tokens.resolve` maps it back
    #: to this record, so nothing else needs to know an intake id exists.
    #: Minted at `create`, replaced wholesale by `relink`, never edited in
    #: place: a copy of an old link a client bookmarked has to stop meaning
    #: anything the moment a new one is issued.
    #:
    #: `exclude=True`: this is a bearer credential, not a field. `GET
    #: /api/intakes` and `GET /api/intakes/{id}` carry no admin check by
    #: their own design (any member may read the queue), and both serialize
    #: an `Intake` straight onto the wire via `response_model` - without this,
    #: the very token about to gate an unauthenticated route would be
    #: readable by any signed-in member. `model_dump`/`model_dump_json`
    #: honour this unconditionally, including a call-time `include={...}`
    #: that names it explicitly - there is no override - so `_write` and
    #: `advance` (the two places inside this module that round-trip an
    #: `Intake` through a dump) both restore it by hand afterwards. A studio
    #: still needs to read the link somehow; that is a later task's job, not
    #: this field's.
    token: str = Field(default="", exclude=True)
    token_expires_at: str = ""

    # What the client said. Kept verbatim, never rewritten.
    client_email: str = ""
    client_phone: str = ""
    scope: str = ""
    budget_text: str = ""

    #: What kind of work the client says this is, and - for `other` - their own
    #: word for it. One of `kinds.BY_ID`, or empty when they have not answered.
    #:
    #: Their own fields rather than two more keys inside `preset`, and that is a
    #: security decision rather than a tidiness one. `preset` is the studio's
    #: whole PAD configuration - currency, market, tax basis, payment terms,
    #: tiers - and it is deliberately absent from `ADVANCE_FIELDS`. Putting the
    #: client's answer in there would mean adding `preset` to that allowlist,
    #: which would hand an anonymous caller the ability to rewrite every pricing
    #: setting the studio chose, to change one of them. These two are what
    #: `ADVANCE_FIELDS`'s own note anticipates: "the day a second write path
    #: needs a field the first should not be able to touch."
    #:
    #: `App.tsx`'s `readPreset` prefers these over `preset.kind` when they are
    #: set, so the pad opens on the client's answer and falls back to the
    #: studio's default for a request generated before this existed.
    client_kind: str = ""
    client_kind_label: str = ""

    #: What the client attached, as a manifest rather than as bytes. One entry
    #: per stored file, in the shape the intake file adapter's `save()` returns and is the
    #: only producer of: `{"id": str, "name": str, "kind": str, "bytes": int,
    #: "note": str}` - the server-minted file id, what the client called it, the
    #: canonical content type, the size, and `attachments.read`'s warning when
    #: there was one ("this scan has no text layer"), empty otherwise.
    #:
    #: Deliberately no URL and no bucket key. Both are functions of this intake's
    #: id and the file's, resolved at read time by `intakefiles`, so a presigned
    #: URL - which expires in minutes - can never be the thing on file, and
    #: moving from local disk to Spaces does not have to rewrite history.
    #:
    #: `List[dict]` is what the plan specifies and it is worth being plain about
    #: what that does and does not buy: `model_validate` enforces "a list, of
    #: dicts", and nothing at all about those five keys. The shape holds because
    #: `intakefiles.save()` is the only thing that builds an entry, not because
    #: this line checks it.
    attachments: List[dict] = Field(default_factory=list)

    #: The PAD settings this intake will be quoted under - kind, currency,
    #: market region, tax basis, payment terms, tiers.
    preset: dict = Field(default_factory=dict)

    # What actually happened.
    job_id: str = ""
    bundle_ids: List[str] = Field(default_factory=list)
    document_id: str = ""
    #: The scope and budget as they stood when Generate was pressed. Kept apart
    #: from the client's own words so the pair reads as "asked" and "priced".
    priced_scope: str = ""
    priced_budget: str = ""
    error: str = ""

    #: What the client asked to change, in the order they asked it - a log,
    #: unlike `priced_scope`, because "what has this client asked for" is a
    #: history a studio needs whole, not just its latest line. Each entry is
    #: `{"asked": ..., "at": ...}`.
    revisions: List[dict] = Field(default_factory=list)
    #: Which of `bundle_ids` was actually sent to the client. A re-quoted
    #: intake can have produced more than one candidate bundle by the time
    #: `send` runs, so this says which one the client saw rather than
    #: leaving a reader to guess `bundle_ids[0]`.
    sent_bundle_id: str = ""
    #: When `send` actually ran - the moment `QUOTED` became `SENT`. Empty in
    #: every state before that, including a quotation that has been prepared
    #: but not yet handed to the client: `app.clientview` reads this, never
    #: `bundle.created_at`, because a studio can sit on a finished quotation
    #: for days before sending it and the client must be told when they were
    #: actually shown something, not when it was made.
    sent_at: str = ""

    #: When the CLIENT pressed Send - the moment `ISSUED` became `SUBMITTED`.
    #:
    #: Not `created_at`, and the difference is the whole reason this exists.
    #: `created_at` is stamped by `create()` when the STUDIO mints the link,
    #: which can be days or weeks before anybody fills it in. The queue was
    #: showing that against the word "SUBMITTED", so a request that arrived
    #: this morning read as having arrived whenever its link was made.
    #:
    #: Empty on every record written before this field existed, and on every
    #: state before `submitted`. Readers must fall back rather than assume it
    #: is there - `IntakeListScreen` does exactly that.
    submitted_at: str = ""

    closed_at: str = ""
    closed_by: str = ""


_lock = threading.RLock()


def _directory():
    return workspaces.root() / DIRNAME


def _path(intake_id: str):
    """Former JSON location, retained for migration and path-safety checks."""
    key = (intake_id or "").strip().lower()
    if not storage.is_valid_id(key):
        return None
    return _directory() / f"{key}.json"


def _write(entry: Intake) -> Intake:
    path = _path(entry.id)
    if path is None:
        raise IntakeError(f"{entry.id!r} is not a usable intake id.")
    if entry.state == CLOSED:
        # A closed request is withdrawn, through whichever of the two doors
        # got it there - `close()`, or `advance(id, CLOSED)`, which is a
        # legal move from every state in `ALLOWED`. Blanked here, in the one
        # place both doors write through, rather than in each caller
        # separately: that is exactly what let `advance(..., CLOSED)`
        # restore the token `close()` had learned to blank - two places
        # implementing one invariant, and only one of them kept up to date.
        # `tokens._build_locked`'s walk re-indexes any intake with a
        # non-empty `token` with no state check, by design, so this is the
        # only place that can reliably keep a withdrawn link withdrawn -
        # `tokens.py` itself still learns nothing about state.
        entry.token = ""
    # `token`'s `exclude=True` keeps it off the wire, and `model_dump` honours
    # that here too. The database is the one place that setting must not reach,
    # so the bearer token is restored explicitly and copied to the indexed
    # lookup column.
    payload = entry.model_dump(mode="json")
    payload["token"] = entry.token
    try:
        database.put(
            workspaces.current(),
            RECORD_KIND,
            entry.id,
            payload,
            sort_key=entry.created_at,
            lookup_key=entry.token,
        )
    except SQLAlchemyError as exc:
        raise IntakeWriteError(f"That intake could not be saved: {exc}") from exc
    if entry.state == CLOSED:
        # The other half of the decision the token blank above makes, in the
        # same place and for the same reason: a withdrawn request must not go
        # on holding a stranger's scope document, and `close()` and
        # `advance(id, CLOSED)` are two equally legal doors to this state.
        # Putting this in `close()` alone is precisely how the token blanking
        # drifted the first time - one door kept up to date, the other not.
        #
        # After the write, never before it: a failed `_write` raises, and files
        # deleted ahead of an intake that then did not close would be gone with
        # nothing recording that they ever existed. `forget()` never raises and
        # never has to be waited on for correctness, so a bucket that cannot be
        # reached costs a log line and some storage, not the close.
        #
        # What this costs, stated correctly rather than as first drafted. It is
        # not `_lock`: every caller of `close()` and `advance()` is inside an
        # `async def` handler or a task started from one, so the lock is
        # effectively uncontended and holding it longer costs nothing. What a
        # slow `forget()` blocks is the **event loop**. `app/presentation/api/intakes.py`'s
        # `close_intake` is `async def` and calls `close()` inline, so a
        # blocking socket here parks every request this worker holds - including
        # an anonymous client's `POST /api/client/{token}/submit`, which cannot
        # even be routed. That client's browser gives up, they retry, and the
        # retry lands on an intake that is already `submitted`, which `/submit`
        # refuses for ever: one admin closing an unrelated enquiry costing a
        # different client their one shot at the link.
        #
        # Which is why `intakefiles._client()` is built with an explicit connect
        # timeout, read timeout and retry bound rather than botocore's defaults
        # (60s/60s/5 attempts, roughly ten minutes across `forget()`'s two round
        # trips). Bounded, the worst case here is seconds. See
        # `intakefiles.SPACES_CONNECT_TIMEOUT_SECONDS` and its comment.
        #
        # Moving this to a thread would not have fixed it on its own - it would
        # only have moved the wait onto `_lock`, where it would block the next
        # `advance()` instead of the loop. Bounding is the fix; a threadpool
        # hand-off on top of it is a reasonable later refinement, not a
        # substitute.
        intakefiles.forget(entry.id)
    return entry


def _later(days: int) -> str:
    """`days` from now, in the same ISO-8601-with-`Z` shape `storage.utc_now_iso`
    produces - `token_expires_at` has to compare against that shape the same
    way `members._expired` already relies on for invitations."""
    when = datetime.now(timezone.utc) + timedelta(days=days)
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create(
    *,
    client_email: str,
    client_phone: str,
    scope: str,
    budget_text: str,
    preset: dict,
    created_by: str,
) -> Intake:
    """Record a request, and mint the link the client will use to see it.

    Starts at `issued`, not `submitted`: Stage 2 gives every intake a link
    from the moment it exists, whether the studio typed the client's words in
    directly - the only way this route works today - or, once a later task
    rewires it, collected nothing but a PAD preset and left the client to
    supply the rest through that link. Either way there is now something to
    wait for - the client opening it - which `submitted` never had to
    describe.
    """
    token = tokens.mint()
    entry = Intake(
        id=storage.new_id(),
        state=ISSUED,
        created_at=storage.utc_now_iso(),
        created_by=created_by,
        client_email=client_email.strip(),
        client_phone=client_phone.strip(),
        scope=scope.strip(),
        budget_text=budget_text.strip(),
        preset=dict(preset or {}),
        token=token,
        token_expires_at=_later(LIFETIME_DAYS),
    )
    with _lock:
        written = _write(entry)
    # Outside the lock: `remember` takes its own, and holding two locks
    # across a call into another module invites a deadlock the moment
    # either one's locking order changes. The gap this opens - committed to
    # SQL but not yet in the process cache - is harmless either way it closes: in
    # this process, `remember` runs a moment later; if the process dies in
    # between, the token is simply one `tokens.resolve` hasn't seen yet, and
    # the next miss uses the database's indexed token lookup regardless.
    tokens.remember(token, workspaces.current(), written.id)
    return written


def relink(intake_id: str) -> Intake:
    """Issue a fresh link for an intake, killing the one before it outright.

    Not a state change - a link a client lost, or one a studio wants to
    resend after `LIFETIME_DAYS`, has nothing to do with how far the
    conversation has actually got. `token_expires_at` is reset from now
    rather than extended from whatever was left of the original, so a relink
    genuinely buys another `LIFETIME_DAYS`, not just what remained of the
    first one.

    Refused outright for a `CLOSED` intake, rather than letting it degrade
    silently: `_write` blanks the token of anything `CLOSED` the moment it
    is written, so minting a new one here and writing it through would erase
    it again immediately - a no-op that looks like success and hands back an
    `Intake` whose `token` does not match what is actually in SQL.
    """
    with _lock:
        entry = get(intake_id)
        if entry is None:
            raise IntakeError("That request does not exist.")
        if entry.state == CLOSED:
            raise IntakeError("A closed request has no link to reissue.")
        old_token = entry.token
        entry.token = tokens.mint()
        entry.token_expires_at = _later(LIFETIME_DAYS)
        written = _write(entry)

    # Outside the lock, for the same reason `create` does it - and the same
    # gap if the process dies here: SQL already has the new
    # token and no longer has the old one, so the next miss's lazy walk
    # lands on the correct, current state either way.
    tokens.forget_token(old_token)
    tokens.remember(written.token, workspaces.current(), written.id)
    return written


def get(intake_id: str) -> Intake | None:
    key = (intake_id or "").strip().lower()
    if not storage.is_valid_id(key):
        return None
    found = database.get(workspaces.current(), RECORD_KIND, key)
    if found is None:
        return None
    try:
        return Intake.model_validate(found.payload)
    except ValueError as exc:
        logger.warning("Unreadable intake %s in the database - leaving it alone: %s", key, exc)
        return None


def exists(intake_id: str) -> bool:
    """Whether an intake row exists, even if its payload cannot be validated."""
    key = (intake_id or "").strip().lower()
    return storage.is_valid_id(key) and database.exists(
        workspaces.current(), RECORD_KIND, key
    )


def listing() -> List[Intake]:
    """Every intake in this workspace, newest first."""
    found: List[Intake] = []
    for record in database.listing(
        workspaces.current(), RECORD_KIND, newest_first=True
    ):
        if not storage.is_valid_id(record.record_id):
            continue
        try:
            found.append(Intake.model_validate(record.payload))
        except ValueError as exc:
            logger.warning("Ignoring unreadable intake %s: %s", record.record_id, exc)
    return found


def advance(intake_id: str, to: str, **fields) -> Intake:
    """Move one intake, or refuse. Everything a state needs is set in the same
    write, so an intake is never briefly in a state without its own evidence."""
    with _lock:
        entry = get(intake_id)
        if entry is None:
            raise IntakeError("That request does not exist.")
        if to not in ALLOWED:
            raise IntakeError(f"{to} is not a state.")
        if to not in ALLOWED.get(entry.state, set()):
            raise IntakeError(f"A request that is {entry.state} cannot become {to}.")

        unknown = set(fields) - ADVANCE_FIELDS
        if unknown:
            raise IntakeError(f"advance() cannot set {sorted(unknown)}.")

        # Revalidated as a whole rather than `setattr` on the live object: a
        # plain `setattr` skips type checking entirely (`Intake` does not turn
        # on `validate_assignment`), so a wrong-shaped value - `bundle_ids` as
        # a string, say - would sit on the object un-checked, get written to
        # disk as something `Intake` cannot read back, and take the record
        # down with it. Building a new instance from a dict means a bad field
        # fails here, before anything is written, and the stored copy is
        # untouched.
        updated = entry.model_dump()
        # `token`'s `exclude=True` drops it from the dump just taken - `fields`
        # can never reintroduce it (it is not in `ADVANCE_FIELDS`), so without
        # this line every single `advance()` call would silently reset a live
        # client link to `""` the moment the record round-tripped.
        updated["token"] = entry.token
        updated.update(fields)
        updated["state"] = to
        try:
            moved = Intake.model_validate(updated)
        except ValidationError as exc:
            raise IntakeError(f"That move would leave the intake invalid: {exc}") from exc
        return _write(moved)


def close(intake_id: str, by: str) -> Intake:
    """Not going ahead. Allowed from anywhere that is not already closed."""
    with _lock:
        entry = get(intake_id)
        if entry is None:
            raise IntakeError("That request does not exist.")
        if entry.state == CLOSED:
            return entry
        # Captured before `_write` blanks it - `advance(id, CLOSED)` is the
        # other, equally legal door to this same state, so the blanking
        # itself lives once in `_write`, keyed on `entry.state`, rather than
        # here: two places restoring or clearing the same field is exactly
        # how `advance` and `close` drifted apart the first time.
        old_token = entry.token
        entry.state = CLOSED
        entry.closed_at = storage.utc_now_iso()
        entry.closed_by = by
        written = _write(entry)
    # Outside the lock, for the same reason `create` and `relink` call into
    # `tokens` outside theirs.
    tokens.forget_token(old_token)
    return written


def forget(workspace_id: str) -> None:
    """Called when a workspace is deleted. Workspace ids are reusable, so an
    intake that outlived its workspace would surface inside somebody else's."""
    # Nothing about an intake's own record is cached in memory here, and
    # `workspaces.delete` has already removed the folder these live in. This
    # exists so the call site reads completely and so a future cache on this
    # module cannot be added without a place to clear it - `tokens._index` is
    # exactly that cache for intake *tokens*, which is why `workspaces.delete`
    # calls `tokens.forget_workspace` as its own separate step rather than
    # folding it in here.
    logger.info("Intakes forgotten with workspace %s", workspace_id)
