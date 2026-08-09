import { useCallback, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { formatBytes } from '../../lib/format'
import { INLINE_KINDS } from '../../types'
import type { ClientSubmitFiles } from '../../lib/clientApi'

/**
 * The client's own picker, for the one form a stranger holding a link can fill
 * in.
 *
 * This is deliberately **not** `ImageDropzone` generalised, and the reason is a
 * rule rather than a preference: the two do not share a policy. The studio's
 * picker admits any file whose type begins `image/` and any of its own document
 * suffixes, which is right for a studio attaching its own reference material to
 * its own brief. This door is anonymous, and what it takes has to be the closed
 * allowlist `main.py`'s `_CLIENT_TYPES` enforces - which excludes
 * `image/svg+xml`, because an SVG is a script document and the studio will
 * later open it on the studio's own origin. A picker built by loosening the
 * studio's rule would offer a client a file the server then refuses; one built
 * by tightening it is this file. Nothing here is copied from that component -
 * not its `DOC_SUFFIXES`, not its `isDocument`, and not its `image/` prefix
 * test - because reusing any of the three would be reusing the wrong rule.
 *
 * `slotFor` below is the whole of the split, and it is a line-by-line mirror of
 * what the server decides, in the same order: `intakefiles.resolve_type`, then
 * both clauses of the gate at the top of `_read_client_files`. That mirroring
 * is the point. A
 * client must never be told one thing by the picker and another by the server,
 * and the only way to keep that true is for the browser's rule to be the
 * server's rule written down again in TypeScript, with the server named at each
 * step so the pair can be checked against each other by reading.
 *
 * What it does *not* mirror is the signature check (`_SIGNATURES`, which refuses
 * an `.exe` renamed `.pdf`) or any of the caps' second enforcement. Those are
 * the door's, they stay the door's, and a browser is not where a lie about a
 * file gets caught. The caps are repeated here only so that a client hears
 * about a 12 MB file before spending three minutes uploading it, never as the
 * thing that stops one.
 *
 * There are no thumbnails, and their absence is a choice with two payoffs. Half
 * of what a client attaches here is a PDF, which has nothing to preview
 * anyway; and a list of compact rows costs a fraction of the height of a grid
 * of 104px tiles, on the one screen in this product with a measured budget for
 * it (`ClientShell`'s own comment on `max-h-full`). It also means no
 * `createObjectURL`, so there is no object URL to leak and no revoke-on-unmount
 * effect to get wrong.
 */

// --- what this door takes, which is what `main.py` takes ---------------------

/** `config.MAX_CLIENT_FILES`. One number across both fields, exactly as the
 *  server counts it - `_read_client_files` walks images and documents as a
 *  single list precisely so six cannot become twelve. */
const MAX_FILES = 6

/** `config.MAX_CLIENT_FILE_BYTES`, 6 MiB. */
const MAX_FILE_BYTES = 6 * 1024 * 1024

/** `config.MAX_CLIENT_UPLOAD_TOTAL_BYTES`, 20 MiB - the whole submission, and
 *  the same number `_gate` bounds the request body against before routing. */
const MAX_TOTAL_BYTES = 20 * 1024 * 1024

/**
 * The five rasters, and nothing else, come from `types.ts`'s `INLINE_KINDS` -
 * imported, not written out again. That file is the single TypeScript
 * statement of `intakefiles.INLINE_TYPES`, and this door held a second copy
 * until a review noticed: two correct copies are how a sixth raster gets added
 * server-side and ships accepted by one screen and refused by the other, which
 * is the same drift the plan cites against itself for once writing the
 * allowlist as four types in one section and five in another.
 *
 * Importing it is not the same as sharing a policy with `ImageDropzone`, which
 * the docstring above refuses on purpose. That component's rule is a *different
 * rule* - the loose `image/` prefix. This is the *same set*, named by the same
 * server constant, and there is nothing for this door to decide about it.
 *
 * What stays this door's own is how the set is reached: a file lands in it by
 * being **declared** one of them and no other way. `resolve_type` refuses to
 * let a filename suffix resolve into it, so a client naming their file
 * `payload.png` cannot decide that it renders. That asymmetry is reproduced
 * faithfully in `slotFor` below rather than smoothed over, which is why a
 * `.heic` that arrives with no type at all (some browsers send none) is refused
 * here rather than accepted and refused again by the server.
 */

/** `intakefiles.FALLBACK_TYPE`. Not a type - that module's word for "no idea",
 *  and on this door the answer that *is* the refusal. */
const FALLBACK_TYPE = 'application/octet-stream'

/**
 * `main.py`'s `_MACRO_SPREADSHEET`, subtracted for the reason written out
 * beside it there: a macro-enabled workbook is a program with a spreadsheet
 * wrapped round it, `Content-Disposition: attachment` is no defence because the
 * download *is* the delivery, and a client with a spreadsheet has `.xlsx`,
 * which carries every cell of the same data and cannot carry the macro.
 *
 * It has to be recognised here in order to be refused. Dropping it from the
 * table entirely would let `book.xlsm` fall through to the suffix step and,
 * worse, let a file *named* `.pdf` but declared this type resolve to
 * `application/pdf` and be accepted - so it is spelled out, resolved, and then
 * refused by its absence from `CLIENT_TYPES`, which is exactly the order the
 * server does it in.
 */
const MACRO_SPREADSHEET = 'application/vnd.ms-excel.sheet.macroEnabled.12'

/**
 * `intakefiles.CONTENT_TYPES` - every type the store knows, and the single
 * suffix each one is stored under.
 *
 * Transcribed rather than derived, because in the browser there is no store to
 * derive it from; the comments name the server's own constant at each step so
 * the two can be compared by reading, which is the only check available.
 *
 * **The capital E in `macroEnabled` is load-bearing and is copied deliberately.**
 * `resolve_type` lower-cases the declared type before it looks it up in this
 * table, and this is the only key here that is not already lower case - so a
 * part *declaring* the macro type matches nothing and falls through to the
 * suffix step, on both ends, for the same reason. Normalising the key here
 * would make this picker resolve a declared macro type that the server does
 * not, which is a divergence in the direction that matters. The refusal itself
 * does not depend on any of that: `.xlsm` reaches this type by its suffix and
 * is then refused by `CLIENT_TYPES`, which is the road a real browser actually
 * takes.
 */
const CONTENT_TYPES: Record<string, string> = {
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/webp': '.webp',
  'image/gif': '.gif',
  'image/heic': '.heic',
  'application/pdf': '.pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
  [MACRO_SPREADSHEET]: '.xlsm',
  'text/csv': '.csv',
  'text/plain': '.txt',
  'text/markdown': '.md',
  [FALLBACK_TYPE]: '.bin',
}

/** `main.py`'s `_CLIENT_TYPES`: what the store knows, less the two subtractions
 *  this door makes. Derived here the same way it is derived there, so adding a
 *  type above cannot silently widen what a stranger may send. */
const CLIENT_TYPES = new Set(
  Object.keys(CONTENT_TYPES).filter(
    (type) => type !== FALLBACK_TYPE && type !== MACRO_SPREADSHEET,
  ),
)

/** `intakefiles._TYPE_FOR_SUFFIX`: the table above read backwards, plus the one
 *  alias a browser might send. Only the canonical suffix is ever stored, so the
 *  alias matters solely on the way in. */
const TYPE_FOR_SUFFIX: Record<string, string> = Object.fromEntries([
  ...Object.entries(CONTENT_TYPES).map(([type, suffix]) => [suffix, type]),
  ['.jpeg', 'image/jpeg'],
])

/**
 * `main.py`'s `_DOCUMENT_READER`: which of `attachments.py`'s readers each
 * accepted document's extension names.
 *
 * The point of it is that a document's extension has to **agree** with its
 * declared type rather than merely be recognised. `scope.txt` carrying PDF
 * bytes and declaring `application/pdf` resolves to `application/pdf`
 * correctly, but `attachments.read` keys its reader off the *name* - so the
 * text reader would run over PDF bytes, decode them as mojibake, find "text"
 * and report no problem at all, and the manifest would assert a clean read of a
 * file nothing read. Comparing readers rather than strings catches that while
 * still allowing the ordinary disagreements: a `.md` a browser declared
 * `text/plain` is a text file either way.
 *
 * Derived from `CLIENT_TYPES`, exactly as the server derives it - which is why
 * `.xlsm` is absent, and why a macro workbook cannot get in by declaring the
 * plain spreadsheet type either.
 */
const SUFFIX_READERS: Record<string, string> = {
  '.pdf': 'pdf',
  '.docx': 'docx',
  '.xlsx': 'xlsx',
  '.xlsm': 'xlsx',
  '.csv': 'text',
  '.txt': 'text',
  '.md': 'text',
}
const DOCUMENT_READER: Record<string, string> = Object.fromEntries(
  Object.entries(CONTENT_TYPES)
    .filter(([type]) => CLIENT_TYPES.has(type) && !INLINE_KINDS.has(type))
    .map(([, suffix]) => [suffix, SUFFIX_READERS[suffix]]),
)

/**
 * What the file dialog offers, and it mirrors `resolve_type`'s own asymmetry
 * rather than listing everything twice: **types** for the images, because only
 * a declared type can earn a file the raster half, and **suffixes** for the
 * documents, because that is the half a suffix is allowed to decide. Offering
 * `.png` here would invite a file the classifier below then refuses, which is
 * the exact contradiction this component exists to avoid - and `.xlsm` is
 * absent for the same reason, from the same derivation.
 *
 * It is a filter on a dialog, not a control: drag-and-drop ignores it entirely,
 * and every dialog has an "All files" escape. `slotFor` is what actually
 * decides.
 */
const ACCEPT = [...INLINE_KINDS, ...Object.keys(DOCUMENT_READER)].join(',')

/** Which of the server's two file fields a file belongs in, or `null` when it
 *  belongs in neither. */
type Slot = 'image' | 'document'

function suffixOf(name: string): string {
  const at = name.lastIndexOf('.')
  return at === -1 ? '' : name.slice(at).toLowerCase()
}

/**
 * The split, once. A transcription of `intakefiles.resolve_type` followed by
 * the two-clause gate at the top of `_read_client_files`, in the server's own
 * order.
 *
 * @returns the field to send it under, or `null` for a file this door refuses
 */
function slotFor(file: File): Slot | null {
  // `resolve_type` splits on ';' and lower-cases before it compares, because a
  // part may arrive as `text/plain; charset=utf-8`.
  const declared = (file.type || '').split(';')[0].trim().toLowerCase()
  const suffix = suffixOf(file.name)

  // `resolve_type`, in its three steps.
  //
  // 1. The declared type wins when the store knows it and it is not the
  //    fallback. For the five rasters this is the only way in, and the file
  //    needs no particular name: a photo pasted as `image.png` and one called
  //    `IMG_4021` are the same file.
  // 2. Otherwise the suffix, and only ever to something *outside* the raster
  //    set - an extension is chosen by whoever uploaded the file, and letting
  //    one earn `inline` would mean a client naming their file `payload.png`
  //    decides that it renders. This is why a `.heic` that arrives with no
  //    declared type at all (some browsers send none) is refused rather than
  //    quietly accepted.
  // 3. Neither answered, so it is the fallback.
  let kind = ''
  if (declared !== FALLBACK_TYPE && declared in CONTENT_TYPES) {
    kind = declared
  } else {
    const guessed = TYPE_FOR_SUFFIX[suffix] || ''
    if (guessed && !INLINE_KINDS.has(guessed)) kind = guessed
  }

  // The gate's first clause. The fallback fails this by construction, and so
  // does the macro workbook, however it was declared or named.
  if (!kind || !CLIENT_TYPES.has(kind)) return null

  if (INLINE_KINDS.has(kind)) return 'image'

  // The gate's second clause: for anything outside the raster set, the reader
  // the extension names must be the reader the resolved type names. A document
  // with no usable extension has no reader at all, which is not equal to one.
  return DOCUMENT_READER[suffix] !== undefined &&
    DOCUMENT_READER[suffix] === DOCUMENT_READER[CONTENT_TYPES[kind]]
    ? 'document'
    : null
}

// --- what a refusal says -----------------------------------------------------

/**
 * The `_WRONG_KIND` sentence from `main.py`, in this page's voice.
 *
 * Same vocabulary deliberately - a PDF, a Word document, a spreadsheet, a CSV
 * or a text file, or a photo as one of the five rasters - so a client who
 * somehow reaches the server's own refusal is not reading a second, different
 * list of what they may send. Two differences, both on purpose: it does not say
 * "PRISM", because nothing on this page ever does (a client is talking to a
 * studio, and the software's name is not theirs to learn), and it names neither
 * `.xlsm` nor `image/heic` explicitly - "a spreadsheet" and "a photo" cover
 * both, and the server's own message does the same.
 */
const WRONG_KIND =
  'not a kind of file that can be sent here — a PDF, a Word document, a ' +
  'spreadsheet, a CSV or a text file, or a photo as PNG, JPEG, WebP, GIF or HEIC'

/**
 * The one refusal that says more than the server's would, and the only place
 * this file deliberately diverges from it.
 *
 * `_WRONG_KIND` covers the macro workbook too, and would tell somebody whose
 * `budget.xlsm` was just refused that they may send "a spreadsheet" - true of
 * the format PRISM takes and baffling to the person reading it. Both ends still
 * refuse the file, so this cannot produce the contradiction the rest of the
 * file exists to prevent; it only replaces a correct sentence with a useful
 * one, and the fix it names is the same one `_MACRO_SPREADSHEET`'s own comment
 * argues costs the client nothing.
 */
const MACRO_REFUSED = 'a macro-enabled workbook — save it as .xlsx and attach that instead'

/** Whether this is the file `MACRO_REFUSED` is about, by either road it could
 *  have arrived on. Compared case-insensitively, unlike the resolution in
 *  `slotFor` - see `CONTENT_TYPES` on why that distinction exists and why this
 *  is the one place it should not be reproduced. */
function isMacroWorkbook(file: File): boolean {
  return (
    suffixOf(file.name) === '.xlsm' ||
    (file.type || '').split(';')[0].trim().toLowerCase() === MACRO_SPREADSHEET.toLowerCase()
  )
}

/** One file on the plate. No object URL: see this file's own docstring. */
type Picked = {
  key: string
  file: File
  slot: Slot
}

/** A file that was not attached, and the sentence saying why. Held per file
 *  rather than as one message on the form, because a client who chose four
 *  things needs to know which one to swap. */
type Refusal = {
  key: string
  name: string
  reason: string
}

let seq = 0

type ClientDropzoneProps = {
  /** Id of the real `<input type="file">`, so a `<label htmlFor>` outside this
   *  component names it. */
  id: string
  onChange: (picked: ClientSubmitFiles) => void
  disabled?: boolean
}

export default function ClientDropzone({ id, onChange, disabled = false }: ClientDropzoneProps) {
  const [items, setItems] = useState<Picked[]>([])
  const [refused, setRefused] = useState<Refusal[]>([])
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)

  // A render-time mirror of `items`, not a latch. `accept` below is handed a
  // whole `FileList` at once and has to decide each file against the ones
  // already on the plate *and* the ones it has just admitted from the same
  // drop, which `items` cannot answer because this render's state is a
  // snapshot. Assigned during render rather than in an effect so it is correct
  // by the time any event handler can read it - and it survives StrictMode's
  // simulated remount harmlessly, because unlike a "have I run once" flag there
  // is nothing about it that a second mount would get wrong: it is overwritten
  // from `items` on every render either way.
  const live = useRef(items)
  live.current = items

  const publish = useCallback(
    (next: Picked[]) => {
      live.current = next
      setItems(next)
      onChange({
        images: next.filter((item) => item.slot === 'image').map((item) => item.file),
        documents: next.filter((item) => item.slot === 'document').map((item) => item.file),
      })
    },
    [onChange],
  )

  const accept = useCallback(
    (fileList: FileList | null) => {
      const incoming = Array.from(fileList || [])
      if (incoming.length === 0) return

      const current = live.current
      const added: Picked[] = []
      const problems: Refusal[] = []
      let total = current.reduce((sum, item) => sum + item.file.size, 0)

      incoming.forEach((file) => {
        seq += 1
        const key = `client-file-${seq}`
        const refuse = (reason: string) => problems.push({ key, name: file.name, reason })

        const slot = slotFor(file)
        if (slot === null) {
          refuse(isMacroWorkbook(file) ? MACRO_REFUSED : WRONG_KIND)
          return
        }
        // The server refuses an empty file by name too, and a browser will
        // happily hand one over - a folder dragged onto the plate arrives as a
        // zero-byte `File` on some platforms.
        if (file.size === 0) {
          refuse('empty — nothing in it to send')
          return
        }
        if (file.size > MAX_FILE_BYTES) {
          refuse(
            `${formatBytes(file.size)} — the limit is ${MAX_FILE_BYTES / (1024 * 1024)} MB per file`,
          )
          return
        }
        if (current.length + added.length >= MAX_FILES) {
          refuse(`${MAX_FILES} files is the limit`)
          return
        }
        if (
          [...current, ...added].some(
            (item) => item.file.name === file.name && item.file.size === file.size,
          )
        ) {
          refuse('already attached')
          return
        }
        // Counted across everything on the plate, which is how `_gate` and
        // `_read_client_files` both count it. Checked after the per-file
        // limit so the more specific sentence wins for a single huge file.
        if (total + file.size > MAX_TOTAL_BYTES) {
          refuse(`over the ${MAX_TOTAL_BYTES / (1024 * 1024)} MB total — send fewer, or smaller`)
          return
        }

        total += file.size
        added.push({ key, file, slot })
      })

      setRefused(problems)
      if (added.length > 0) publish([...current, ...added])
    },
    [publish],
  )

  const remove = useCallback(
    (key: string) => {
      publish(live.current.filter((item) => item.key !== key))
      // Every refusal goes, not the one matching this key - a refusal never
      // shares a key with anything on the plate, because a refused file is by
      // definition not on it. Clearing the lot is also the right answer rather
      // than merely the achievable one: the refusals that survive a removal are
      // exactly the ones removing something has just invalidated. "6 files is
      // the limit" stops being true the moment there are five, and an
      // out-of-date reason sitting under the plate is worse than none, because
      // the client cannot tell it is stale. Picking again produces a fresh set.
      setRefused([])
    },
    [publish],
  )

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    setDragging(false)
    if (disabled) return
    accept(event.dataTransfer ? event.dataTransfer.files : null)
  }

  const onDragOver = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    if (!disabled) setDragging(true)
  }

  const onDragLeave = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    setDragging(false)
  }

  const full = items.length >= MAX_FILES

  return (
    <div>
      {/* Labelled from outside and out of the tab order: the plate below is the
          real control, and a keyboard reaching both would stop on the same
          thing twice. */}
      <input
        ref={inputRef}
        id={id}
        type="file"
        accept={ACCEPT}
        multiple
        tabIndex={-1}
        disabled={disabled}
        className="sr-only"
        onChange={(event) => {
          accept(event.target.files)
          // Cleared so that choosing the same file again after removing it
          // still fires a change event.
          event.target.value = ''
        }}
      />

      <button
        type="button"
        disabled={disabled}
        aria-describedby={`${id}-rules`}
        onClick={() => inputRef.current?.click()}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragEnter={onDragOver}
        onDragLeave={onDragLeave}
        className={[
          'block w-full rounded-xl border px-4 py-3.5 text-left',
          'transition-[border-color,background-color] duration-[150ms] ease-out motion-reduce:transition-none',
          // `ballpoint` is this theme's name for the kit's one accent green and
          // `duplicate` its name for the secondary fill - both are held over
          // from the previous design direction's vocabulary, and renaming them
          // is a change to `index.css`, not to a component that uses them.
          dragging ? 'border-ballpoint bg-accent-soft' : 'border-rule bg-duplicate',
          'disabled:cursor-not-allowed disabled:opacity-70',
        ].join(' ')}
      >
        {/* The affordance and the rules on one line, which is both the better
            design and the measured one.

            Better, because the field already carries a `FieldLabel` above it
            reading "Attachments (optional)": a second uppercase tracked line
            inside the box saying "Drop files or choose them" made two labels
            stacked eight pixels apart, and the box does not need naming twice.

            Measured, because this form is the one screen in the product with a
            stated height budget - `ClientShell`'s own comment on `max-h-full`
            records what happens at the boundary. Two lines here put the card
            three pixels past the 1000px its scroll container actually offers
            at 1080, which costs a scrollbar and clips the card's bottom
            padding for no gain. */}
        <span className="block font-body text-[13px] leading-[1.55]">
          <span className="font-label text-[12px] font-medium uppercase tracking-[0.12em] text-ink">
            {full ? `${MAX_FILES} attached` : 'Drop files or choose them'}
          </span>
          {/* The rules give way to the ceiling once it is reached: "up to 6
              files" is advice for somebody who can still add one, and reading
              it beside six attached files describes a limit as though it were
              an invitation. The id stays on whichever sentence is showing, so
              `aria-describedby` always resolves to text worth hearing. */}
          <span id={`${id}-rules`} className="text-void">
            {full
              ? ' — that is the limit. Remove one to add another.'
              : ` — PDF, Word, Excel, CSV, text or photos, up to ${MAX_FILES} files at ${
                  MAX_FILE_BYTES / (1024 * 1024)
                } MB each.`}
          </span>
        </span>
      </button>

      {refused.length > 0 ? (
        // Attached to the file, not to the form: this list sits under the plate
        // the file was dropped on, and nothing about a refusal touches the three
        // fields the client has already typed into. `role="status"` rather than
        // `alert` for the reason the validation summary in `ClientForm` gives -
        // polite is right for a region that is corrected by picking again, and
        // an assertive one would interrupt whatever is being typed next.
        <ul role="status" className="mt-2.5 space-y-1">
          {refused.map((problem) => (
            <li key={problem.key} className="font-body text-[13px] leading-[1.5] text-alert">
              <span className="text-ink">{problem.name}</span> — not attached: {problem.reason}.
            </li>
          ))}
        </ul>
      ) : null}

      {items.length > 0 ? (
        <ul className="mt-2.5 space-y-1">
          {items.map((item) => (
            <li
              key={item.key}
              className="flex items-center justify-between gap-3 font-body text-[13px] leading-[1.6]"
            >
              <span className="min-w-0 flex-1 truncate text-ink" title={item.file.name}>
                {item.file.name}
              </span>
              <span className="shrink-0 font-label text-[12px] tabular-nums text-faint">
                {formatBytes(item.file.size)}
              </span>
              <button
                type="button"
                disabled={disabled}
                onClick={() => remove(item.key)}
                className="min-h-11 shrink-0 rounded-lg px-2 font-body text-[13px] text-void underline decoration-hairline underline-offset-2 transition-colors duration-[150ms] ease-out hover:text-alert motion-reduce:transition-none disabled:cursor-not-allowed sm:min-h-0 sm:px-1"
              >
                Remove<span className="sr-only"> {item.file.name}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
