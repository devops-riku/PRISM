"""Where a client's own files live, and how little the rest of PRISM knows about it.

One interface, two backends. Configured with DigitalOcean Spaces credentials,
an intake's files are objects in a private bucket; configured with none, the
same bytes are files under the workspace's own `_intake_files/`. Which of those
answers is a deployment fact. Everything above this module - the upload route,
the studio's download route, the generation that reads the files back - is
written against the six functions below and never learns which one it got.

That shape is `app/mailer.py`'s, deliberately, down to `configured()` being a
predicate read live off `config` rather than a boolean latched at import. It
carries the same rule too: **nothing here raises merely because an integration
is absent.** A studio running PRISM on one machine has no object store, wants
none, and must still be able to take a client's scope document.

The local backend is not a stub. It is what lets this feature ship and be
tested before credentials exist, keeps every `check_*.py` offline, and is the
right answer for a single-machine install forever.

**`boto3` is imported inside `_client()`, never at module scope.** `intakes.py`
imports this module and `main.py` imports that, so a top-level import would
make the entire app unimportable on a machine that has not installed boto3 yet
- including every check script in `backend/scripts/`, all of which run offline
with no credentials and none of which should need an S3 library to assert that
a state machine refuses a move.

**What is stored, and what is not.** The bytes, under a server-minted 12-hex
file id and the one extension its content type is kept under. Not the client's
filename, which is manifest data on the record and never a path or a key; not a
URL and not a bucket key, both of which are derived from the two ids at read
time, so a presigned URL cannot go stale on a record and swapping backends does
not rewrite history. The credentials are read through `config` and appear in no
return value, no manifest entry and no log line.

**Addressing.** Both backends resolve `intake_id` through `storage.is_valid_id`
before it can reach a path or a key, exactly as `intakes._path()` does, and the
file id through the same gate. A bucket key is not a filesystem path and `..`
does not traverse it - but the id gate is what stops one intake addressing
another's objects, which is the same attack by a different road, and it is why
the pair is always checked together rather than each alone.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from app import config, storage, workspaces

logger = logging.getLogger("prism.intakefiles")

__all__ = [
    "CONTENT_TYPES",
    "DIRNAME",
    "FALLBACK_TYPE",
    "INLINE_TYPES",
    "IntakeFileError",
    "KEY_PREFIX",
    "VIEW_URL_TTL_SECONDS",
    "configured",
    "disposition_for",
    "endpoint",
    "forget",
    "listing",
    "read",
    "save",
    "view_url",
]

#: The local backend's directory, beside `_intakes/` inside the workspace. The
#: leading underscore is not what keeps it out of anything - `intakes.listing()`
#: globs `*.json` and `storage.all_bundles()` only descends into directories
#: whose names are 12-hex quotation ids, so a folder called `_intake_files` is
#: invisible to both for the same reason `_documents` and `_jobs` are.
DIRNAME = "_intake_files"

#: The first segment of every Spaces key. A bucket may hold more than this app
#: one day; everything PRISM writes is under one prefix so it can be found,
#: lifecycled or removed as a unit.
KEY_PREFIX = "intakes"

#: How long a presigned view URL is good for. Minutes, not hours: the URL is a
#: bearer credential for one private object, it is handed to a studio member who
#: is looking at the file right now, and anything longer is a link that outlives
#: the reason it was minted. Five minutes survives a slow download and a browser
#: that opens the tab a moment later; it does not survive being pasted into a
#: chat and read tomorrow.
VIEW_URL_TTL_SECONDS = 300


class IntakeFileError(RuntimeError):
    """The bytes did not land, or there was nowhere legitimate to put them.

    Raised by `save()` alone. Every read-side function answers a question that
    has a "no" - `read()` returns `None`, `listing()` an empty list, `view_url()`
    an empty string - and `forget()` is contractually silent (see its docstring).
    A caller that could not store a file, though, has to know: the alternative is
    a manifest entry pointing at nothing.
    """


#: Every content type PRISM will store, and the single extension it is kept
#: under. The extension is load-bearing rather than cosmetic: it is the only
#: thing on disk or in the bucket that records what the file is, so `read()`
#: recovers the content type by mapping it back. That means the map has to be a
#: bijection in this direction - one canonical suffix per type - and aliases
#: (`.jpeg`) live in the reverse map below, where they are only ever read.
#:
#: The set is `attachments.SUFFIXES` (what PRISM can extract text from) plus the
#: raster images a client may send. Storing is not accepting: what a client is
#: allowed to upload is the upload route's decision, not this module's.
CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel.sheet.macroEnabled.12": ".xlsm",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/octet-stream": ".bin",
}

#: What a stored extension means, going back the other way. Built from the map
#: above plus the aliases a browser might have sent - only the canonical suffix
#: is ever written, so the aliases matter solely on the way in.
_TYPE_FOR_SUFFIX = {suffix: kind for kind, suffix in CONTENT_TYPES.items()}
_TYPE_FOR_SUFFIX[".jpeg"] = "image/jpeg"

#: What a file with no recognisable type becomes. Not a refusal: this module
#: stores what it is told, and refusing a type is the upload route's job, where
#: there is a person to tell about it.
FALLBACK_TYPE = "application/octet-stream"

#: The raster images - and nothing else - a browser may be asked to render in
#: place. Everything outside this set is written with `Content-Disposition:
#: attachment` so that a file a stranger uploaded cannot execute on the studio's
#: own origin when somebody opens it. `image/svg+xml` is absent on purpose: an
#: SVG is a script document, and this is the set that decides whether a thing
#: renders, not merely whether it is an image.
INLINE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "image/heic"}
)


# --- Which backend is answering ----------------------------------------------


def configured() -> bool:
    """True when all four Spaces credentials are set. Read live, like `mailer`'s.

    All four or none: three of them is a misconfiguration that would fail on the
    first upload rather than at startup, and answering "configured" for it would
    take the local fallback away from a studio who had merely typed one name
    wrong in `.env`.
    """
    return bool(
        config.SPACES_KEY.strip()
        and config.SPACES_SECRET.strip()
        and config.SPACES_REGION.strip()
        and config.SPACES_BUCKET.strip()
    )


def endpoint() -> str:
    """`https://<region>.digitaloceanspaces.com`.

    Derived rather than configured as a fifth variable. The endpoint is a pure
    function of the region for every Space there is, and a separate setting is
    one more thing to get subtly wrong in a way that only shows up as a signature
    mismatch nobody can read.
    """
    region = config.SPACES_REGION.strip().lower()
    return f"https://{region}.digitaloceanspaces.com" if region else ""


def _client():
    """The S3 client Spaces speaks to.

    The one place `boto3` is imported, and the reason it is imported here: see
    this module's docstring. It is also the seam the check script replaces to
    exercise this branch offline, which is a happy side effect of the import
    rule rather than the reason for it.

    A client is built per call. boto3 clients are reusable and caching one is the
    obvious optimisation, but a cache keyed on credentials that can change
    underneath it is a bug waiting to be written, and nothing on this path is hot
    enough yet to pay for it.
    """
    import boto3  # noqa: PLC0415 - lazily, on purpose; see the docstring above

    return boto3.session.Session().client(
        "s3",
        region_name=config.SPACES_REGION.strip(),
        endpoint_url=endpoint(),
        aws_access_key_id=config.SPACES_KEY.strip(),
        aws_secret_access_key=config.SPACES_SECRET.strip(),
    )


def _bucket() -> str:
    return config.SPACES_BUCKET.strip()


# --- Names, types and the gate every id goes through -------------------------


def _clean_name(name: str) -> str:
    """The client's filename, as a name and only ever as a name.

    Nothing downstream builds a path or a key from this - `save()` mints an id
    for that - so this is not a security boundary. It is here because the string
    is written onto the record and rendered in the studio's queue, and a
    "filename" of `..\\..\\etc\\passwd` in that column is at best confusing and
    at worst something a future reader treats as a path because it looks like
    one. Directory separators are stripped, control characters with them, and
    the whole thing is capped at the same 120 characters `attachments.read`
    already caps its own copy at.
    """
    text = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(character for character in text if character >= " " and character != "\x7f")
    return text.strip()[:120] or "attachment"


def _suffix_of(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def _resolve_type(kind: str, name: str) -> Tuple[str, str]:
    """The canonical content type and the suffix it is stored under.

    The declared type wins when PRISM knows it. When it does not, the filename's
    own suffix is consulted - not as a second opinion but for the reason
    `attachments.SUFFIXES` is keyed on suffixes in the first place: browsers
    disagree about the mime for `.docx` and `.xlsx`, and several send
    `application/octet-stream` for both. A `.docx` that arrives with no usable
    type is still a `.docx`, and treating it as one loses nothing, because what
    the type decides here is the extension and the disposition, and the
    disposition for every non-raster is `attachment` either way.
    """
    declared = (kind or "").split(";")[0].strip().lower()
    if declared in CONTENT_TYPES:
        return declared, CONTENT_TYPES[declared]

    guessed = _TYPE_FOR_SUFFIX.get(_suffix_of(name))
    if guessed:
        return guessed, CONTENT_TYPES[guessed]

    return FALLBACK_TYPE, CONTENT_TYPES[FALLBACK_TYPE]


def _type_of_stored(filename: str) -> str:
    """What a stored `<file_id>.<ext>` says it is."""
    return _TYPE_FOR_SUFFIX.get(_suffix_of(filename), FALLBACK_TYPE)


def _stem_of(key: str) -> str:
    """The `<file_id>` out of a `.../<file_id>.<ext>` bucket key.

    Split by hand rather than through `Path(key).stem`. A bucket key is not a
    filesystem path: it is always `/`-separated, on every platform, and
    `pathlib` on Windows would additionally treat a backslash in a key as a
    separator - which is a thing a key may legitimately contain. That it happens
    to give the right answer for the keys this module writes is a coincidence of
    the dev machine, and the deployment target is not the dev machine.
    """
    name = key.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def disposition_for(kind: str) -> str:
    """`inline` for the raster allowlist, `attachment` for everything else.

    Set on the object at write time rather than decided on every read, so the
    route that serves a presigned URL does not have to be in the path for the
    header to be right. See `INLINE_TYPES`.
    """
    return "inline" if (kind or "").split(";")[0].strip().lower() in INLINE_TYPES else "attachment"


def _intake_key(intake_id: str) -> Optional[str]:
    """The intake id, lowercased, or `None` for anything that is not a bare
    12-character hex string - the same guard `intakes._path()` applies for the
    same reason, before a caller-supplied id can become a path segment or a
    bucket key."""
    key = (intake_id or "").strip().lower()
    return key if storage.is_valid_id(key) else None


def _file_key(file_id: str) -> Optional[str]:
    """The same gate on the other half of the pair. A file id reaches a `glob`
    on the local backend and a `list_objects_v2` prefix on Spaces, and neither
    should ever be handed a `*` or a `..`."""
    key = (file_id or "").strip().lower()
    return key if storage.is_valid_id(key) else None


def _local_dir(intake_id: str) -> Optional[Path]:
    key = _intake_key(intake_id)
    return None if key is None else workspaces.root() / DIRNAME / key


def _prefix(intake_id: str) -> Optional[str]:
    """`intakes/<workspace>/<intake>/`, or `None` for an id that is not usable.

    The workspace is part of the key for the same reason it is part of every
    path in `generated/w/`: two workspaces share nothing, and an intake id is
    only unique within one.

    Having no workspace at all is a different thing from having a bad id, and is
    raised rather than returned so the two backends answer it identically: the
    local side reaches `workspaces.root()`, which raises `NoWorkspace` for the
    same condition, and every other persisting module in this codebase lets that
    propagate rather than inventing somewhere to file the work. `forget()` is
    the one caller that must not raise, and it catches this along with
    everything else.
    """
    key = _intake_key(intake_id)
    if key is None:
        return None
    workspace = workspaces.current()
    if not workspace:
        raise workspaces.NoWorkspace(
            "There is no workspace yet. Create one and this will have somewhere to live."
        )
    return f"{KEY_PREFIX}/{workspace}/{key}/"


# --- Writing -----------------------------------------------------------------


def save(intake_id: str, name: str, data: bytes, kind: str, *, note: str = "") -> dict:
    """Store one file and return the manifest entry for it.

    The returned dict is the shape `Intake.attachments` holds, the shape
    `clientview.of` projects a subset of, and the shape the frontend mirrors:

        {"id": str, "name": str, "kind": str, "bytes": int, "note": str}

    `id` is minted here and is the only way the file is ever addressed. `name` is
    what the client called it, kept because a studio needs to recognise the thing
    they were sent. `kind` is the canonical content type - the same string this
    object is written with and the same string `read()` gives back. `bytes` is
    what actually arrived. `note` is the caller's, and is where
    `attachments.read`'s warning ends up when there is one ("this scan has no
    text layer"); empty otherwise.

    There is no URL and no bucket key in it, on purpose. Both are functions of
    the two ids, so a record written today still resolves after the bucket moves,
    and a presigned URL - which expires - can never be the thing on file.
    """
    key = _intake_key(intake_id)
    if key is None:
        raise IntakeFileError(f"{intake_id!r} is not a usable intake id.")

    file_id = storage.new_id()
    filename = _clean_name(name)
    content_type, suffix = _resolve_type(kind, filename)
    payload = bytes(data or b"")

    if configured():
        _spaces_put(key, f"{file_id}{suffix}", payload, content_type)
    else:
        _local_put(key, f"{file_id}{suffix}", payload)

    logger.info(
        "Stored %d bytes of %s for intake %s as %s%s",
        len(payload),
        content_type,
        key,
        file_id,
        suffix,
    )
    return {
        "id": file_id,
        "name": filename,
        "kind": content_type,
        "bytes": len(payload),
        "note": (note or "").strip(),
    }


def _local_put(intake_id: str, filename: str, data: bytes) -> None:
    directory = _local_dir(intake_id)
    if directory is None:  # pragma: no cover - `save` gated the id already
        raise IntakeFileError(f"{intake_id!r} is not a usable intake id.")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_bytes(data)
    except OSError as exc:
        raise IntakeFileError(f"That file could not be saved: {exc}") from exc


def _spaces_put(intake_id: str, filename: str, data: bytes, content_type: str) -> None:
    prefix = _prefix(intake_id)
    if prefix is None:  # pragma: no cover - `save` gated the id already
        raise IntakeFileError(f"{intake_id!r} is not a usable intake id.")
    try:
        _client().put_object(
            Bucket=_bucket(),
            Key=f"{prefix}{filename}",
            Body=data,
            # Said rather than inherited. Spaces defaults new objects to private,
            # and a default is a thing somebody can change in a control panel
            # without ever reading this file.
            ACL="private",
            ContentType=content_type,
            # Both of these are set here, once, on write - not on the route that
            # serves the file. A presigned GET goes straight from the browser to
            # DigitalOcean with this app nowhere in the path, so a header this
            # app would have added on the way out would simply not be there.
            ContentDisposition=disposition_for(content_type),
            CacheControl="no-store",
        )
    except Exception as exc:  # noqa: BLE001 - botocore's errors are not ours to enumerate
        # Logged with the detail, raised without it. A botocore error names the
        # bucket and the endpoint, and `main.py` turns an unexpected failure into
        # a 500 whose detail a caller can read - including, on the anonymous
        # upload route, a caller who is a stranger with a link.
        logger.error("Could not write %s%s to Spaces: %s", prefix, filename, exc)
        raise IntakeFileError("That file could not be saved.") from exc


# --- Reading -----------------------------------------------------------------


def _local_file(intake_id: str, file_id: str) -> Optional[Path]:
    """The one file this pair names, or `None`.

    The glob pattern is built from an id that has already been through
    `storage.is_valid_id`, so it contains no metacharacter and can match nothing
    but `<that id>.<something>` inside `<that intake>`'s own directory. A file id
    belonging to another intake resolves to a directory it is not in, and comes
    back `None` - which is the cross-intake refusal, arrived at structurally
    rather than by a comparison somebody could forget to write.
    """
    directory = _local_dir(intake_id)
    key = _file_key(file_id)
    if directory is None or key is None or not directory.is_dir():
        return None
    for path in sorted(directory.glob(f"{key}.*")):
        if path.is_file():
            return path
    return None


def _spaces_objects(prefix: str) -> Iterator[dict]:
    """Every object under one prefix, following the continuation token.

    Paged by hand rather than through boto3's paginator so that the whole Spaces
    branch is reachable behind one replaceable `_client()`; a paginator is built
    from the client and would need a second seam to stand in for.
    """
    client = _client()
    token = ""
    while True:
        arguments = {"Bucket": _bucket(), "Prefix": prefix}
        if token:
            arguments["ContinuationToken"] = token
        answer = client.list_objects_v2(**arguments)
        for item in answer.get("Contents", []) or []:
            yield item
        if not answer.get("IsTruncated"):
            return
        token = answer.get("NextContinuationToken", "")
        if not token:
            return


def _spaces_object_key(intake_id: str, file_id: str) -> Optional[str]:
    """The full key this pair names, or `None`. The extension is not known to
    the caller - only the id is on the record - so it is recovered by listing the
    one-object prefix the pair produces."""
    prefix = _prefix(intake_id)
    key = _file_key(file_id)
    if prefix is None or key is None:
        return None
    try:
        for item in _spaces_objects(f"{prefix}{key}."):
            return item["Key"]
    except Exception as exc:  # noqa: BLE001 - an unreachable bucket is a "no", not a crash
        logger.warning("Could not look up %s for intake %s: %s", key, intake_id, exc)
    return None


def read(intake_id: str, file_id: str) -> Optional[Tuple[bytes, str]]:
    """The file's bytes and its content type, or `None`.

    `None` covers every way the answer is no, and they are deliberately not
    distinguished: a malformed id, an id that is fine but names nothing, a file
    id that belongs to a different intake, and a file that could not be read just
    now all come back the same. A caller cannot tell "that is not yours" from
    "that does not exist", which is the only honest thing to tell somebody
    guessing.
    """
    if configured():
        key = _spaces_object_key(intake_id, file_id)
        if key is None:
            return None
        try:
            answer = _client().get_object(Bucket=_bucket(), Key=key)
            body = answer["Body"].read()
        except Exception as exc:  # noqa: BLE001 - as above
            logger.warning("Could not read %s: %s", key, exc)
            return None
        return body, (answer.get("ContentType") or _type_of_stored(key))

    path = _local_file(intake_id, file_id)
    if path is None:
        return None
    try:
        return path.read_bytes(), _type_of_stored(path.name)
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def listing(intake_id: str) -> List[dict]:
    """Every file actually in storage for this intake, as `{id, kind, bytes}`.

    Not the manifest, and deliberately a narrower shape than `save()` returns.
    The client's filename and the extraction note are the *record's* - they live
    on `Intake.attachments`, because storage never held them - and filling them
    in as empty strings here would hand back something that looked like a
    manifest entry for a file with no name. What this answers is "what is on the
    other end", which is a different question from "what does the record say",
    and the two being answerable separately is the point.
    """
    if configured():
        prefix = _prefix(intake_id)
        if prefix is None:
            return []
        try:
            found = [
                {
                    "id": _stem_of(item["Key"]),
                    "kind": _type_of_stored(item["Key"]),
                    "bytes": int(item.get("Size", 0)),
                }
                for item in _spaces_objects(prefix)
                if storage.is_valid_id(_stem_of(item["Key"]))
            ]
        except Exception as exc:  # noqa: BLE001 - an unreachable bucket lists nothing
            logger.warning("Could not list the files for intake %s: %s", intake_id, exc)
            return []
        return sorted(found, key=lambda row: row["id"])

    directory = _local_dir(intake_id)
    if directory is None or not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not storage.is_valid_id(path.stem):
            continue
        try:
            size = path.stat().st_size
        except OSError:  # pragma: no cover - removed between the walk and the stat
            continue
        found.append({"id": path.stem, "kind": _type_of_stored(path.name), "bytes": size})
    return found


def view_url(intake_id: str, file_id: str) -> str:
    """Where the studio can look at this file, or `""` when there is no such file.

    On Spaces, a presigned `GET` good for `VIEW_URL_TTL_SECONDS`. On local, the
    path of the app's own download route - one thing for the frontend to link to
    either way, so it never learns which backend is behind it.

    The pair is resolved to a real object *before* anything is signed. A
    presigned URL is a bearer credential for a private object and minting one is
    the disclosure, whatever the caller does with it afterwards - so a file id
    from another intake has to come back empty here, not merely 404 later.
    """
    if configured():
        key = _spaces_object_key(intake_id, file_id)
        if key is None:
            return ""
        try:
            return _client().generate_presigned_url(
                "get_object",
                Params={"Bucket": _bucket(), "Key": key},
                ExpiresIn=VIEW_URL_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - as above
            logger.warning("Could not sign a URL for %s: %s", key, exc)
            return ""

    path = _local_file(intake_id, file_id)
    if path is None:
        return ""
    # Relative on purpose: the frontend prefixes its own API base, and this
    # server does not reliably know the origin it is being reached on.
    return f"/api/intakes/{_intake_key(intake_id)}/files/{_file_key(file_id)}"


# --- Deletion ----------------------------------------------------------------


def forget(intake_id: str) -> int:
    """Remove every file this intake has, and never raise doing it.

    Returns how many this call is sure it removed. Called from `intakes._write`
    the moment a record becomes `closed`, and that is the whole reason for the
    "never raises" rule: closing a request is what a studio pressed the button
    for, and a bucket that cannot be reached in that moment is a fact about the
    network, not a reason to leave a withdrawn intake open. Everything that goes
    wrong here is logged, at the level it deserves, and the count comes back
    honest.
    """
    removed = 0
    try:
        removed = _spaces_forget(intake_id) if configured() else _local_forget(intake_id)
    except Exception as exc:  # noqa: BLE001 - this function's contract is that it returns
        logger.error(
            "Could not delete the files for intake %s - it is closed either way: %s",
            intake_id,
            exc,
        )
    if removed:
        logger.info("Deleted %d file(s) with intake %s", removed, intake_id)
    return removed


def _local_forget(intake_id: str) -> int:
    directory = _local_dir(intake_id)
    if directory is None or not directory.is_dir():
        return 0
    removed = 0
    for path in sorted(directory.iterdir()):
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Could not delete %s: %s", path, exc)
    try:
        directory.rmdir()
    except OSError:
        # Something is still in there - a file that would not unlink, or a
        # directory nothing here put there. The files are the point; an empty
        # folder left behind is not worth failing a close over.
        pass
    return removed


def _delete_batch(client, keys: List[dict]) -> int:
    try:
        answer = client.delete_objects(Bucket=_bucket(), Delete={"Objects": keys, "Quiet": True})
    except Exception as exc:  # noqa: BLE001 - see `forget`
        logger.error("Could not delete %d object(s) from Spaces: %s", len(keys), exc)
        return 0
    for failure in answer.get("Errors", []) or []:
        logger.error(
            "Spaces refused to delete %s: %s",
            failure.get("Key", "?"),
            failure.get("Message", "no reason given"),
        )
    return len(keys) - len(answer.get("Errors", []) or [])


#: What one `delete_objects` call may carry. The S3 API's own limit, not ours.
_DELETE_BATCH = 1000


def _spaces_forget(intake_id: str) -> int:
    prefix = _prefix(intake_id)
    if prefix is None:
        return 0
    client = _client()
    removed = 0
    batch: List[dict] = []
    for item in _spaces_objects(prefix):
        batch.append({"Key": item["Key"]})
        if len(batch) >= _DELETE_BATCH:
            removed += _delete_batch(client, batch)
            batch = []
    if batch:
        removed += _delete_batch(client, batch)
    return removed
