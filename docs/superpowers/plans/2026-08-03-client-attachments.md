# Client Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A client attaches documents and images to their intake through the same link they fill the form in; the studio reviews the originals before pricing, and both reach the model when the quotation is generated.

**Architecture:** `POST /api/client/{token}/submit` becomes multipart. Files are written under a new per-workspace `_intake_files/<intake_id>/` directory under server-minted names, never the client's own. The record carries a manifest, not the bytes. A new authed route serves them back to the studio, and `POST /api/proposals` reads them off disk when it is given an `intake_id` — so a client's file never travels browser→server→browser→server.

**Tech Stack:** FastAPI + Pydantic v2, React 18 + TypeScript strict, Tailwind v4. No new dependencies — `python-docx`, `openpyxl` and the PDF reader are already present and already used by `attachments.py`.

## Global Constraints

- **This widens the first anonymous write surface in the codebase from a JSON body to a file upload.** Task 1 is a precondition, not a nicety: it lands before any upload route exists.
- **The client's filename never becomes a path.** Files are stored as `<12-hex>.<ext>` with the original name kept in the manifest as data. `storage.is_valid_id` is the existing gate for this shape and is reused.
- **A link buys one upload.** `/submit` is legal only from `issued` and moves the intake to `submitted`, so `intakes.ALLOWED` already bounds an anonymous caller to one submission per token. Do not add a second anonymous write route; that bound is the strongest control in this feature.
- **Nothing client-supplied is ever served `inline` except a raster image from a closed allowlist.** `image/svg+xml` is refused outright — an SVG is a script document, and the studio opens these on the studio's own origin.
- **Every response carrying a client file sets `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.**
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
- Modify: `backend/app/intakes.py`, `backend/app/config.py`
- Test: `backend/scripts/check_intakefiles.py`

**Interfaces:**
- Produces: `intakefiles.save(intake_id, name, data, kind) -> dict` (the manifest entry), `intakefiles.listing(intake_id) -> List[dict]`, `intakefiles.read(intake_id, file_id) -> tuple[bytes, str] | None`, `intakefiles.forget(intake_id) -> int`. `intakes.Intake.attachments: List[dict]`.

- [ ] **Step 1: The directory, gated like every other caller-supplied id.** `_intake_files/<intake_id>/` beside `_intakes/`, resolved through `storage.is_valid_id` exactly as `intakes._path()` does — the same guard, for the same reason, and `listing()`'s `glob("*.json")` means the new directory is invisible to the intake walk either way. The file id is a fresh 12-hex, minted server-side; the client's filename is stored as a manifest string and never touches a path.

- [ ] **Step 2: The manifest field.** `Intake.attachments: List[dict] = Field(default_factory=list)`, each entry `{id, name, kind, bytes, note}`. `note` carries the extraction warning when there is one ("this scan has no text layer") and is empty otherwise. Add `attachments` to `ADVANCE_FIELDS` — `/submit` writes it through `_client_advance`, and `advance()` revalidates through `Intake.model_validate`, so a malformed entry is refused at the write rather than at the read.

- [ ] **Step 3: The caps, separate from the studio's.** `config.MAX_CLIENT_FILES`, `MAX_CLIENT_FILE_BYTES`, `MAX_CLIENT_UPLOAD_TOTAL_BYTES`. Deliberately tighter than `MAX_IMAGES`/`MAX_DOCUMENTS`, and deliberately their own names: a studio raising its own limit must not raise a stranger's.

- [ ] **Step 4: Deletion.** `close()` removes the directory. A closed intake blanks its token already (`_write`'s chokepoint); the files are the other half of the same decision. `forget` returns a count and never raises — a file that will not delete is logged, not surfaced to whoever pressed Close.

- [ ] **Step 5: Prove the traversal cases.** `../`, a drive-absolute segment, an id that is not 12-hex, an empty id, a file id from another intake. Each returns `None` rather than reaching a path.

- [ ] **Step 6: Commit**

---

## Task 3: The client writes files

**Files:**
- Modify: `backend/app/main.py` (`submit_client_intake`, `ClientSubmitRequest`), `backend/app/clientview.py`
- Test: `backend/scripts/check_client_upload.py`

- [ ] **Step 1: `/submit` becomes multipart.** The four fields become `Form(...)`; `images` and `documents` become `File(default=[])` with the same loose `List[Union[UploadFile, str]]` typing `_read_images`/`_read_documents` already use — and for the reason their docstrings give, which is that a browser posts an empty file input as a part with no filename and a strict `List[UploadFile]` makes FastAPI reject the whole request before any handler runs. `ClientSubmitRequest` is deleted or kept only if something still reads it.

- [ ] **Step 2: Reuse the studio's readers, then diverge where the caller is a stranger.** `_read_images`/`_read_documents` already do count, declared-size and read-bounded-size gating; call them. Then add what the anonymous path needs and the studio path does not: an explicit raster allowlist. `_read_images` admits anything matching `image/`, which includes `image/svg+xml` — a script document the studio will later open on the studio's own origin. The client path accepts `image/png`, `image/jpeg`, `image/webp`, `image/gif`, `image/heic` and nothing else.

- [ ] **Step 3: Extract, save, manifest — in that order, inside the borrow.** `attachments.read()` for the extraction warning, `intakefiles.save()` for the bytes, the returned entries into `_client_advance(..., attachments=[...])`. All of it before `give_back`, exactly as every other anonymous handler does it.

- [ ] **Step 4: Orphans.** A save that succeeds followed by an `advance` that is refused leaves bytes on disk with nothing pointing at them. Decide the order that cannot orphan — or clean up on the failure path — and write down which.

- [ ] **Step 5: The client sees what they sent.** `clientview.of` gains the filenames on the waiting face. Names and sizes only: no ids, no URLs, nothing that addresses the file. A client who attached the wrong thing needs to know it arrived, which is a different need from being able to fetch it back.

- [ ] **Step 6: Prove the refusals.** An SVG. A `.exe` renamed `.pdf`. A file past the per-file cap. More files than the count cap. A total past the aggregate cap. A second submit against the same token. Each refused, and the intake unchanged after every one.

- [ ] **Step 7: Commit**

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

- [ ] **Step 2: The headers, which are the point of the route.** `X-Content-Type-Options: nosniff` and `Cache-Control: no-store` on every response. `Content-Disposition: inline` only for the raster allowlist Task 3 enforced; `attachment` for everything else. Serve the stored mime, never a sniffed one.

- [ ] **Step 3: Refuse the cross-intake fetch.** A file id belonging to a different intake, in the same workspace or another, is a 404 — the id pair must be checked together, not each alone.

- [ ] **Step 4: The queue shows them.** Filenames on the row, each a link. A `submitted` row whose client attached three files and a `submitted` row with none must be distinguishable without opening either.

- [ ] **Step 5: Typecheck, build, and open a client-uploaded file from the queue.** Quote what you observed, including the response headers.

- [ ] **Step 6: Commit**

---

## Task 6: The files reach the model

**Files:**
- Modify: `backend/app/main.py` (`create_proposal`)

- [ ] **Step 1: Read them server-side when `intake_id` is given.** `POST /api/proposals` already takes `intake_id: str = Form("")`. When it is present and names a real intake in this workspace, load that intake's stored files and merge them with whatever the pad itself uploaded. The client's file must not make a second round trip through the studio's browser.

- [ ] **Step 2: Merge, do not replace.** A studio that attaches its own reference material while pricing a client's request keeps both. The combined set is bounded by the studio's own `MAX_IMAGES`/`MAX_DOCUMENTS`, not the client's tighter caps — say which limit reports the overflow and make its message name the cause.

- [ ] **Step 3: A missing file is reported, not raised.** A file deleted from under a running generation is one line on the finished quotation, exactly as an unreadable PDF already is.

- [ ] **Step 4: Walk the whole feature once, end to end.** Generate a link, attach a document and an image as the client, price it as the studio, and confirm both reached the quotation. **This is the one place a live Gemini call is warranted** — one call, on a scratch `GENERATED_DIR`. Quote what you observed.

- [ ] **Step 5: Run every `check_*.py` except `check_kind_render.py`, plus `smoke.py`, then commit**

---

## Self-review

**Spec coverage.** The user's choice was "Documents + images, files kept": documents and images both reach the model (Tasks 3, 6), the files are kept (Task 2), and the studio reviews the originals before pricing (Task 5). The security precondition the choice implies is Task 1.

**What this plan deliberately does not do.** No virus scanning — out of scope and there is no scanner in this stack. No retention window beyond `close()` — files live as long as the intake does. No thumbnailing; the queue links files, it does not preview them. No client-side deletion after submit: `/submit` runs once from `issued`, and adding a second anonymous write to undo it would give away the bound that makes this feature safe.

**Type consistency.** `intakefiles.save()`'s returned dict (Task 2) is the entry shape `Intake.attachments` holds, the shape `clientview.of` projects a subset of (Task 3), and the shape `types.ts` mirrors (Task 5). One shape, four places, named in Task 2's Interfaces block.

**The three checks that carry this plan:** Task 1's "a chunked body is refused before it is read", Task 3's "an SVG is refused", and Task 2's "a file id from another intake returns None". If those three passed and nothing else did, the anonymous surface would still be sound.

**Standing risk this plan inherits rather than fixes.** The branch it builds on has four open Criticals from the whole-branch review — the non-atomic `_write`, the non-retroactive rate-column removal, and the lost-response face. None blocks this feature, and none is fixed by it. `_write`'s truncation is the one to watch: this plan adds a manifest to the record it truncates, so a failed write now loses the pointer to files that are still on disk.
