# Client Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A client attaches documents and images to their intake through the same link they fill the form in; the studio reviews the originals before pricing, and both reach the model when the quotation is generated.

**Architecture:** `POST /api/client/{token}/submit` becomes multipart. Files go to **DigitalOcean Spaces** under server-minted keys, never the client's own filename. The record carries a manifest, not the bytes. A new authed route lets the studio see them, and `POST /api/proposals` fetches them itself when given an `intake_id` — so a client's file never travels browser→server→browser→server.

**Tech Stack:** FastAPI + Pydantic v2, React 18 + TypeScript strict, Tailwind v4. **One new dependency: `boto3`**, for the S3-compatible API Spaces speaks.

## Storage: Spaces, with local disk as the unconfigured fallback

Two backends behind one interface, the same shape `mailer.py` already uses for Resend: a `configured()` predicate, and a documented degradation when the answer is no.

- **Configured** (`SPACES_KEY`, `SPACES_SECRET`, `SPACES_REGION`, `SPACES_BUCKET` all set) → objects go to Spaces at `intakes/<workspace>/<intake_id>/<file_id>.<ext>`.
- **Not configured** → the same bytes go to `generated/w/<workspace>/_intake_files/<intake_id>/`, exactly as the first draft of this plan described.

This is not hedging. It is what lets the feature ship and be tested before credentials exist, keeps every `check_*.py` offline and network-free, and means a studio running PRISM on one machine needs no object store at all. The interface is the contract; which backend answers is a deployment fact.

**The upload is proxied through the backend, never presigned-direct from the browser.** A presigned `PUT` handed to an anonymous client is a credential to write arbitrary bytes to the bucket until it expires, with no server-side check of type, size or content in between — and it routes around the body cap Task 1 exists to enforce. The file is validated and text-extracted first, then written.

**The bucket is private. The studio reads through short-lived presigned `GET` URLs, never public-read objects.** A client's scope document must not be readable by anyone who guesses a URL.

**Credentials are read through `config` and never logged, never returned by any route, and never written into the manifest.** `/api/health` may report *whether* Spaces is configured, exactly as it already reports `key_configured` for Gemini — never the key.

## Global Constraints

- **This widens the first anonymous write surface in the codebase from a JSON body to a file upload.** Task 1 is a precondition, not a nicety: it lands before any upload route exists.
- **The client's filename never becomes a path.** Files are stored as `<12-hex>.<ext>` with the original name kept in the manifest as data. `storage.is_valid_id` is the existing gate for this shape and is reused.
- **A link buys one upload.** `/submit` is legal only from `issued` and moves the intake to `submitted`, so `intakes.ALLOWED` already bounds an anonymous caller to one submission per token. Do not add a second anonymous write route; that bound is the strongest control in this feature.
- **Nothing client-supplied is ever served `inline` except a raster image from a closed allowlist.** `image/svg+xml` is refused outright — an SVG is a script document, and the studio opens these on the studio's own origin.
- **Every response carrying a client file sets `Cache-Control: no-store`, an explicit `Content-Type`, and `Content-Disposition: attachment` for anything outside the raster allowlist.** `X-Content-Type-Options: nosniff` is added **on the local branch only, and this is a deliberate, reasoned exception rather than an oversight** — see below.

### Why nosniff is not on the Spaces branch

It cannot be. `PutObject` accepts `ACL`, `CacheControl`, `ContentDisposition`, `ContentEncoding`, `ContentLanguage`, `ContentType`, `Expires`, `Metadata` and the SSE members; the presign overrides are the `Response*` set. **No member on either side emits `X-Content-Type-Options`**, and `Metadata` emits `x-amz-meta-*` only. Since a presigned GET is fetched browser→DigitalOcean with this app out of the path, there is nowhere left to add it.

The first draft of this plan asserted the opposite and Task 5 would have collided with it. Recorded here rather than discovered there.

What stands in for it, and why it is adequate: nosniff's job is to stop a browser treating a mislabelled file as HTML or script. Every object is written with an **explicit, server-resolved `Content-Type`** — never absent, never guessed from the bytes — so there is nothing for a browser to sniff *into*. Everything outside the raster allowlist is written `Content-Disposition: attachment`, which is not rendered at all. And the allowlist is exactly `intakefiles.INLINE_TYPES` — `image/png`, `image/jpeg`, `image/webp`, `image/gif`, `image/heic`, five formats that cannot execute — with `image/svg+xml` refused outright, which is the case nosniff would actually have been protecting against.

**`INLINE_TYPES` is the single source of truth for that set.** An earlier draft of this section wrote it out as four types while Task 3 Step 2 wrote five, which is exactly the drift that gets a file stored under one rule and served under another. Import it; do not restate it.

Practical note for Task 5, not a security one: Safari renders HEIC and Chrome and Firefox do not, so `inline` on a `.heic` degrades to a download in most browsers. That is a display outcome, not a hole — HEIC cannot execute either way.

The residual is a browser ignoring a correct `Content-Type` on a five-format raster allowlist. That is thin, it is on a different origin from the studio's session, and buying it back would mean streaming every byte through this process and giving up the 307 entirely. **If that trade is ever revisited, revisit it here rather than silently in a route.**
- **A file that cannot be read is reported, never raised.** This is `attachments.py`'s existing rule (its module docstring states it) and it now applies to a client who is not in the room to be asked.
- `backend/app/schemas.py` is not modified. TypeScript strict, zero errors, zero `any`/`as`/`@ts-ignore`/`!`. Tailwind v4 CSS-first. No test framework may be added; backend checks are standalone scripts under `backend/scripts/` that exit 0.
- Branch from `e5b581f` on `feat/client-attachments`.

**Interpreter:** `backend/.venv/Scripts/python.exe`, run from `backend`.

**Do not run `backend/scripts/check_kind_render.py`** — date-pinned baseline hash, red at every commit by design.

**Never write to `backend/generated/`** — it holds the studio's real work. Use a scratch `GENERATED_DIR`.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `backend/app/intakefiles.py` | Write, list, read and delete one intake's stored files |
| `backend/scripts/check_intakefiles.py` | Storage, id gating, caps, traversal, deletion |
| `backend/scripts/check_client_upload.py` | The anonymous multipart surface, including what it refuses |
| `frontend/src/components/client/ClientDropzone.tsx` | The client's own picker |

**Modified**

| File | Change |
|---|---|
| `backend/app/main.py` | `_gate`'s body cap; `/submit` becomes multipart; the authed download route; `/api/proposals` reads stored files |
| `backend/app/attachments.py` | Bound decompression before a zip is opened |
| `backend/app/intakes.py` | `attachments` manifest field; `ADVANCE_FIELDS`; delete files on `close` |
| `backend/app/config.py` | The client-side caps, separate from the studio's |
| `backend/app/clientview.py` | The client sees what they attached |
| `frontend/src/lib/clientApi.ts` | `FormData` on submit; a longer timeout for it alone |
| `frontend/src/components/client/ClientForm.tsx` | The dropzone, and its errors |
| `frontend/src/components/IntakeListScreen.tsx` | The queue shows and links the attachments |
| `frontend/src/types.ts` | The manifest type |
| `frontend/src/lib/api.ts` | The authed download URL |

---

## Task 1: The cap that actually holds, and the zip that cannot bomb

**Files:**
- Modify: `backend/app/main.py` (`_gate`, ~309-313), `backend/app/attachments.py`
- Test: `backend/scripts/check_intake_gate.py` (extend), `backend/scripts/check_intakefiles.py` (create, first assertions)

This task ships **before** any upload route exists, because both defects it closes get materially worse the moment one does. Neither is hypothetical: the first was measured at 200 MiB buffered on a single request against a bogus token, the second is reachable by any `.docx` an anonymous caller sends.

- [x] **Step 1: Write the failing check — a chunked body is refused.** `_gate` currently reads `content-length` and does nothing when the header is absent, so `Transfer-Encoding: chunked` skips the cap entirely and Starlette buffers the whole body before the handler runs. Assert that a chunked POST to `/api/client/<bogus>/submit` carrying more than the cap is refused **before** the body is fully read.

- [x] **Step 2: Make it pass.** The declared-length check stays — it is the cheap path and it already works. Add the undeclared case: read the body in bounded chunks and refuse at the limit. `request.stream()` is the hook; the handler must still be able to read the body afterwards, so whatever is consumed has to be replayed or the refusal has to happen without consuming. Say in a comment which you chose and why.

- [x] **Step 3: Raise the cap for multipart, and only for multipart.** A JSON submit and a 20 MB upload cannot share one number. Gate on `content-type`: `multipart/form-data` gets `MAX_CLIENT_UPLOAD_TOTAL_BYTES`, everything else keeps the existing JSON cap.

- [x] **Step 4: Write the failing check — a zip bomb is refused.** `attachments.py` hands client bytes straight to `docx.Document()` and `openpyxl.load_workbook()`; both are zip readers with no decompression bound, so a 1 MB upload can expand to gigabytes. Build a small zip whose uncompressed size is far past any sane document and assert it is refused rather than read.

- [x] **Step 5: Make it pass.** Before opening a `.docx`/`.xlsx`, inspect `zipfile.ZipFile(...).infolist()` and sum `file_size`; refuse past a bound. Report it the way this module reports everything — an `Attachment` carrying the reason, not an exception.

- [x] **Step 6: Run both scripts and `smoke.py`, then commit**

---

## Task 2: Where a client's file lives

**Files:**
- Create: `backend/app/intakefiles.py`
- Modify: `backend/app/intakes.py`, `backend/app/config.py`, `backend/requirements.txt`
- Test: `backend/scripts/check_intakefiles.py`

**Interfaces:**
- Produces: `intakefiles.configured() -> bool`, `intakefiles.save(intake_id, name, data, kind) -> dict` (the manifest entry), `intakefiles.listing(intake_id) -> List[dict]`, `intakefiles.read(intake_id, file_id) -> tuple[bytes, str] | None`, `intakefiles.view_url(intake_id, file_id) -> str` (presigned, or the local route's own path), `intakefiles.forget(intake_id) -> int`. `intakes.Intake.attachments: List[dict]`.

- [ ] **Step 1: One interface, two backends.** Everything above is defined once and dispatches on `configured()`. Read `backend/app/mailer.py` first — its `configured()` predicate and its documented behaviour when the answer is no are the shape to copy, including that nothing raises merely because an integration is absent.

  `boto3` is imported **lazily, inside the Spaces backend**, not at module top level. Every `check_*.py` runs offline with no credentials, and a hard import would make the whole module — and so `intakes.py`, and so the app — unimportable on a machine that has not installed it yet.

- [ ] **Step 2: The key, gated like every other caller-supplied id.** Spaces: `intakes/<workspace_id>/<intake_id>/<file_id>.<ext>`. Local: `_intake_files/<intake_id>/` beside `_intakes/`. Both resolve `intake_id` through `storage.is_valid_id` exactly as `intakes._path()` does — the same guard, for the same reason, and `listing()`'s `glob("*.json")` means the local directory is invisible to the intake walk either way. The file id is a fresh 12-hex minted server-side; **the client's filename is a manifest string and never appears in a key or a path.** A bucket key is not a filesystem path and `..` does not traverse it, but the id gate is what stops one intake addressing another's objects, which is the same attack by a different road.

- [ ] **Step 3: The Spaces config, on the house pattern.** `SPACES_KEY`, `SPACES_SECRET`, `SPACES_REGION`, `SPACES_BUCKET` via `_env_str(name, "")` in `config.py`, with the endpoint derived as `https://<region>.digitaloceanspaces.com` rather than configured separately. `configured()` is all four non-empty, mirroring `mailer.configured()`. **Objects are written private** (`ACL: private` — Spaces defaults to private, but say it explicitly rather than inherit it) with `ContentType` set from the validated type and `ContentDisposition` set to `attachment` for everything the raster allowlist does not cover.

- [ ] **Step 4: The manifest field.** `Intake.attachments: List[dict] = Field(default_factory=list)`, each entry `{id, name, kind, bytes, note}`. `note` carries the extraction warning when there is one ("this scan has no text layer") and is empty otherwise. **No URL and no bucket key in the manifest** — both are derived from the ids at read time, so a presigned URL cannot go stale on the record and a backend swap does not rewrite history. Add `attachments` to `ADVANCE_FIELDS` — `/submit` writes it through `_client_advance`, and `advance()` revalidates through `Intake.model_validate`, so a malformed entry is refused at the write rather than at the read.

- [ ] **Step 5: The caps, separate from the studio's.** `config.MAX_CLIENT_FILES`, `MAX_CLIENT_FILE_BYTES`. (`MAX_CLIENT_UPLOAD_TOTAL_BYTES` already exists — Task 1 added it for the body cap, and it is the same ceiling; do not define a second one.) Deliberately tighter than `MAX_IMAGES`/`MAX_DOCUMENTS`, and deliberately their own names: a studio raising its own limit must not raise a stranger's.

- [ ] **Step 6: Deletion.** `close()` removes the intake's objects. A closed intake blanks its token already (`_write`'s chokepoint); the files are the other half of the same decision. `forget` returns a count and never raises — an object that will not delete is logged, not surfaced to whoever pressed Close. On Spaces that is a `delete_objects` over the listed prefix, and a network failure there must not make `close()` fail: the intake closing is the thing the studio asked for.

- [ ] **Step 7: Prove the id gate both ways.** `../`, a drive-absolute segment, an id that is not 12-hex, an empty id, a file id belonging to another intake. Each returns `None` rather than reaching a path or a key. Run the whole suite against the **local** backend, which is what `configured() == False` gives you offline; that is the point of having it.

- [ ] **Step 8: Commit**

---

## Task 3: The client writes files

**Files:**
- Modify: `backend/app/main.py` (`submit_client_intake`, `ClientSubmitRequest`), `backend/app/clientview.py`
- Test: `backend/scripts/check_client_upload.py`

- [x] **Step 1: `/submit` becomes multipart.** The four fields become `Form(...)`; `images` and `documents` become `File(default=[])` with the same loose `List[Union[UploadFile, str]]` typing `_read_images`/`_read_documents` already use — and for the reason their docstrings give, which is that a browser posts an empty file input as a part with no filename and a strict `List[UploadFile]` makes FastAPI reject the whole request before any handler runs. `ClientSubmitRequest` is deleted or kept only if something still reads it.

- [x] **Step 2: Reuse the studio's readers, then diverge where the caller is a stranger.** `_read_images`/`_read_documents` already do count, declared-size and read-bounded-size gating; call them. Then add what the anonymous path needs and the studio path does not: an explicit raster allowlist. `_read_images` admits anything matching `image/`, which includes `image/svg+xml` — a script document the studio will later open on the studio's own origin. The client path accepts `image/png`, `image/jpeg`, `image/webp`, `image/gif`, `image/heic` and nothing else.

- [x] **Step 3: Extract, save, manifest — in that order, inside the borrow.** `attachments.read()` for the extraction warning, `intakefiles.save()` for the bytes, the returned entries into `_client_advance(..., attachments=[...])`. All of it before `give_back`, exactly as every other anonymous handler does it.

- [x] **Step 4: Orphans.** A save that succeeds followed by an `advance` that is refused leaves bytes on disk with nothing pointing at them. Decide the order that cannot orphan — or clean up on the failure path — and write down which.

- [x] **Step 5: The client sees what they sent.** `clientview.of` gains the filenames on the waiting face. Names and sizes only: no ids, no URLs, nothing that addresses the file. A client who attached the wrong thing needs to know it arrived, which is a different need from being able to fetch it back.

- [x] **Step 6: Prove the refusals.** An SVG. A `.exe` renamed `.pdf`. A file past the per-file cap. More files than the count cap. A total past the aggregate cap. A second submit against the same token. Each refused, and the intake unchanged after every one.

- [x] **Step 7: Commit**

---

## Task 4: The client's picker

**Files:**
- Create: `frontend/src/components/client/ClientDropzone.tsx`
- Modify: `frontend/src/lib/clientApi.ts`, `frontend/src/components/client/ClientForm.tsx`, `frontend/src/types.ts`

- [ ] **Step 1: `clientApi.ts` posts `FormData`.** The submit call alone; the other two stay JSON. Do not set `Content-Type` by hand — the browser sets the multipart boundary and overriding it breaks the parse. This module imports neither `currentWorkspace` nor `accessToken` and that does not change.

- [ ] **Step 2: The timeout that fits an upload.** The shared 15s `AbortController` is right for a JSON write and wrong for 20 MB on hotel wifi: it aborts a request that is progressing. Give the submit call its own, longer bound, and say in a comment why the two differ.

- [ ] **Step 3: The dropzone.** `ImageDropzone.tsx` already splits images from documents at pick time, which is the part worth not writing twice — but its copy is studio-voiced ("Reference material") and its limits are the studio's. Either generalise it or write a client one; do not duplicate the split logic. The copy is the client's: what they can send, how many, how big, in their language.

- [ ] **Step 4: The failure the client will actually hit.** A refused file must say which file and why, next to the picker, without clearing the three fields they have already typed. Attach the message to the file, not to the form.

- [ ] **Step 5: Announce the upload.** The submit button is `disabled={pending}`, which blurs it and drops focus to `<body>`, and there is no live region on this page for the whole in-flight window — a client on a screen reader gets silence. With an upload that window is now long enough to look broken. A `role="status"` that says the upload is running, and focus that does not fall to the document.

- [ ] **Step 6: Typecheck, build, walk it.** Attach a PDF, a PNG and an SVG; the first two land, the third is refused by name. Quote what you observed. **Scratch `GENERATED_DIR`; set `APP_ORIGIN` to match the dev server.**

- [ ] **Step 7: Commit**

---

## Task 5: The studio reads them

**Files:**
- Modify: `backend/app/main.py`, `frontend/src/components/IntakeListScreen.tsx`, `frontend/src/lib/api.ts`, `frontend/src/types.ts`

- [ ] **Step 1: `GET /api/intakes/{intake_id}/files/{file_id}`.** Behind the gate like every other studio route. No admin check — reading the queue is any member's, and `list_intakes`'s own docstring settles that this is the same class of read.

  On Spaces it answers `307` to a **presigned URL with a short TTL** (minutes, not hours) rather than streaming the bytes through this process; on local it serves the file. One route, either way, so the frontend has one thing to link to and never learns which backend is behind it.

- [ ] **Step 2: The headers, which are the point of the route.** `Cache-Control: no-store`, an explicit `Content-Type` from the manifest, `Content-Disposition: inline` only for the raster allowlist Task 3 enforced and `attachment` for everything else. Never a sniffed type.

  For Spaces these are set as **object metadata at `put_object` time** (Task 2 does this), so a presigned GET carries them without this route being in the path. **`X-Content-Type-Options: nosniff` is set on the local branch only** — it cannot be attached to a Spaces object or a presigned URL at all. Do not go looking for a way; read the "Why nosniff is not on the Spaces branch" section above, which settles it and says what stands in for it. If you disagree with that trade, say so in your report rather than changing the design here.

  **Filenames need encoding, not interpolation.** `intakefiles.clean_name` strips separators and control characters but keeps `"`, so a file called `sco"pe.pdf` would break a naive `Content-Disposition: attachment; filename="…"`. Quote it properly or use RFC 5987 `filename*`.

- [ ] **Step 3: Refuse the cross-intake fetch.** A file id belonging to a different intake, in the same workspace or another, is a 404 — the id pair must be checked together, not each alone. **Check it before minting a presigned URL**, not after: a presigned URL is a bearer credential and handing one out is the disclosure, whatever this route returns next.

- [ ] **Step 4: The studio sees them, and this is the half the user asked for by name.** The queue row lists each attachment by filename, with its size, each one a link to the route above. A `submitted` row whose client attached three files and one with none must be distinguishable without opening either.

  The queue row is a summary, so if the full list does not fit it, say how many there are and put the list where the scope already goes. **Do not silently show the first two.**

  **A closed intake still lists its attachments and every one of them is gone.** `close()` deletes the objects but leaves `Intake.attachments` populated, which is per plan — the record is the history of what was sent. A closed row rendered straight from that manifest offers a full set of links that all 404. Decide what a closed row shows and say what you decided.

- [ ] **Step 5: Typecheck, build, and open a client-uploaded file from the queue.** Quote what you observed, including the response headers.

- [ ] **Step 6: Commit**

---

## Task 6: The files reach the model

**Files:**
- Modify: `backend/app/main.py` (`create_proposal`)

- [ ] **Step 1: Read them server-side when `intake_id` is given.** `POST /api/proposals` already takes `intake_id: str = Form("")`. When it is present and names a real intake in this workspace, load that intake's stored files and merge them with whatever the pad itself uploaded. The client's file must not make a second round trip through the studio's browser.

  On Spaces this is a `get_object` per attachment, which is **network I/O on a request that is otherwise CPU-bound**. `create_proposal` is `async def`; do not block the event loop on it. Fetch inside the threadpool, or before the job is handed off — say which and why.

- [ ] **Step 2: Merge, do not replace.** A studio that attaches its own reference material while pricing a client's request keeps both. The combined set is bounded by the studio's own `MAX_IMAGES`/`MAX_DOCUMENTS`, not the client's tighter caps — say which limit reports the overflow and make its message name the cause.

- [ ] **Step 3: A missing file is reported, not raised.** A file deleted from under a running generation is one line on the finished quotation, exactly as an unreadable PDF already is.

- [ ] **Step 4: Walk the whole feature once, end to end.** Generate a link, attach a document and an image as the client, price it as the studio, and confirm both reached the quotation. **This is the one place a live Gemini call is warranted** — one call, on a scratch `GENERATED_DIR`. Quote what you observed.

- [ ] **Step 5: Run every `check_*.py` except `check_kind_render.py`, plus `smoke.py`, then commit**

---

## Self-review

**Spec coverage.** The user's choice was "Documents + images, files kept": documents and images both reach the model (Tasks 3, 6), the files are kept (Task 2), and the studio reviews the originals before pricing (Task 5). The security precondition the choice implies is Task 1.

**What this plan deliberately does not do.** No virus scanning — out of scope and there is no scanner in this stack. No retention window beyond `close()` — files live as long as the intake does. No thumbnailing; the queue links files, it does not preview them. No client-side deletion after submit: `/submit` runs once from `issued`, and adding a second anonymous write to undo it would give away the bound that makes this feature safe. No CDN, no public bucket, no custom domain on the Space — every read is a short-lived presigned URL.

**What Spaces adds that local disk did not, and is now this plan's responsibility.** An upload that reaches Spaces and then fails to land on the record is an orphaned object nobody is paying attention to but the invoice (Task 3 Step 4 owns the ordering). A `close()` that cannot reach the network must still close the intake (Task 2 Step 6). A generation that fetches four files is four network round trips on a path that used to touch no network at all (Task 6 Step 1). And credentials now exist that did not before: they are read through `config`, never logged, never in a response, never in the manifest.

**Type consistency.** `intakefiles.save()`'s returned dict (Task 2) is the entry shape `Intake.attachments` holds, the shape `clientview.of` projects a subset of (Task 3), and the shape `types.ts` mirrors (Task 5). One shape, four places, named in Task 2's Interfaces block.

**The three checks that carry this plan:** Task 1's "a chunked body is refused before it is read", Task 3's "an SVG is refused", and Task 2's "a file id from another intake returns None". If those three passed and nothing else did, the anonymous surface would still be sound.

**Standing risk this plan inherits rather than fixes.** The branch it builds on has four open Criticals from the whole-branch review — the non-atomic `_write`, the non-retroactive rate-column removal, and the lost-response face. None blocks this feature, and none is fixed by it. `_write`'s truncation is the one to watch: this plan adds a manifest to the record it truncates, so a failed write now loses the pointer to files that are still on disk.
