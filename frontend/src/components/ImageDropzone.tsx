import { useCallback, useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'

const MAX_FILES = 8
const MAX_BYTES = 8 * 1024 * 1024
//: Larger than a screenshot on purpose: one of these is usually the whole
//: scope, and a tender pack with drawings in it is not small. Matches
//: config.MAX_DOCUMENT_BYTES on the server, which is where it is enforced.
const MAX_DOC_BYTES = 12 * 1024 * 1024

//: What the server can read as text - see backend/app/attachments.py. Kept as
//: suffixes because browsers disagree about the mime type of a .docx, and the
//: suffix is what the person actually chose.
const DOC_SUFFIXES = ['.pdf', '.docx', '.xlsx', '.xlsm', '.csv', '.txt', '.md']

const isDocument = (file: File) =>
  DOC_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix))

function megabytes(bytes: number) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function readableSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return megabytes(bytes)
}

let seq = 0

/** One file on the plate. `url` is an object URL for an image, '' for a document. */
type Attachment = {
  key: string
  file: File
  document: boolean
  url: string
}

/** A file that was not attached, and the sentence saying why. */
type Rejection = {
  name: string
  reason: string
}

type ImageDropzoneProps = {
  id: string
  onChange: (files: File[]) => void
  disabled?: boolean
}

/**
 * Reference material: pictures and documents, on the same plate.
 *
 * A studio is as often handed a scope in Word or a requirement list in Excel as
 * a photo of a whiteboard, and retyping either into the brief is the tax this
 * removes. The two go different ways on the server - an image is sent to the
 * model as an image, a document is read to text and quoted into the brief - so
 * they are separated here, at the point where the file is chosen, rather than
 * anywhere further in.
 *
 * Eight files, 8 MB for a picture and 12 MB for a document. Previews come from
 * object URLs which are revoked on removal and on unmount so nothing leaks for
 * the life of the session; a document has no preview, and its type is its
 * icon.
 *
 * The real <input> stays labelled but out of the tab order; the plate itself is
 * a <button>, so it is keyboard operable with a visible ballpoint focus ring.
 */
export default function ImageDropzone({ id, onChange, disabled = false }: ImageDropzoneProps) {
  const [items, setItems] = useState<Attachment[]>([])
  const [rejected, setRejected] = useState<Rejection[]>([])
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const liveItems = useRef(items)

  liveItems.current = items

  useEffect(
    () => () => {
      liveItems.current.forEach((item) => item.url && URL.revokeObjectURL(item.url))
    },
    [],
  )

  const accept = useCallback(
    (fileList: FileList | null) => {
      const incoming = Array.from(fileList || [])
      if (incoming.length === 0) return

      const current = liveItems.current
      const added: Attachment[] = []
      const problems: Rejection[] = []
      let room = MAX_FILES - current.length

      incoming.forEach((file) => {
        const image = Boolean(file.type && file.type.startsWith('image/'))
        const document = !image && isDocument(file)

        if (!image && !document) {
          problems.push({
            name: file.name,
            reason: 'not a type PRISM reads — images, PDF, Word, Excel or CSV',
          })
          return
        }

        const ceiling = image ? MAX_BYTES : MAX_DOC_BYTES
        if (file.size > ceiling) {
          problems.push({
            name: file.name,
            reason: `${megabytes(file.size)} — the ceiling is ${ceiling / (1024 * 1024)} MB`,
          })
          return
        }
        const duplicate = [...current, ...added].some(
          (item) => item.file.name === file.name && item.file.size === file.size,
        )
        if (duplicate) {
          problems.push({ name: file.name, reason: 'already attached' })
          return
        }
        if (room <= 0) {
          problems.push({ name: file.name, reason: 'eight images is the ceiling' })
          return
        }
        room -= 1
        seq += 1
        added.push({
          key: `att-${seq}`,
          file,
          document,
          // Only an image has anything to show. Creating an object URL for a
          // 12 MB PDF would hold it in memory to render nothing.
          url: image ? URL.createObjectURL(file) : '',
        })
      })

      const next = [...current, ...added]
      liveItems.current = next
      setItems(next)
      setRejected(problems)
      onChange(next.map((item) => item.file))
    },
    [onChange],
  )

  const remove = useCallback(
    (key: string) => {
      const target = liveItems.current.find((item) => item.key === key)
      if (target) URL.revokeObjectURL(target.url)
      const next = liveItems.current.filter((item) => item.key !== key)
      liveItems.current = next
      setItems(next)
      onChange(next.map((item) => item.file))
    },
    [onChange],
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
      <input
        ref={inputRef}
        id={id}
        type="file"
        accept="image/*,.pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md"
        multiple
        tabIndex={-1}
        disabled={disabled}
        className="sr-only"
        onChange={(event) => {
          accept(event.target.files)
          event.target.value = ''
        }}
      />

      <button
        type="button"
        disabled={disabled}
        aria-describedby={`${id}-rules`}
        onClick={() => inputRef.current && inputRef.current.click()}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragEnter={onDragOver}
        onDragLeave={onDragLeave}
        className={[
          'block w-full rounded-xl border px-4 py-3 text-left',
          'transition-[border-color,background-color] duration-[120ms] motion-reduce:transition-none',
          dragging ? 'border-ballpoint bg-duplicate' : 'border-rule bg-duplicate',
          'disabled:cursor-not-allowed',
        ].join(' ')}
      >
        <span className="block font-label text-[12px] font-medium uppercase tracking-[0.12em] text-ink">
          {full ? 'Eight attached — remove one to add another' : 'Drop files or choose them'}
        </span>
        <span
          id={`${id}-rules`}
          className="mt-1 block font-label text-[11px] uppercase tracking-[0.1em] tabular-nums text-void"
        >
          Images · PDF · Word · Excel — {items.length} of 8
        </span>
      </button>

      {rejected.length > 0 ? (
        <ul role="status" className="mt-3 space-y-1">
          {rejected.map((problem) => (
            <li
              key={`${problem.name}-${problem.reason}`}
              className="font-label text-[12px] tabular-nums text-ballpoint"
            >
              <span className="text-ink">{problem.name}</span> — not attached: {problem.reason}
            </li>
          ))}
        </ul>
      ) : null}

      {items.length > 0 ? (
        <ul className="mt-4 flex flex-wrap gap-4">
          {items.map((item) => (
            <li key={item.key} className="w-[104px]">
              <div className="relative">
                {/* A document has nothing to preview, so it shows what it is
                    instead: the suffix, which is the only part of a filename
                    that survives being truncated to fit. */}
                {item.document ? (
                  <div className="flex h-[72px] w-[104px] flex-col items-center justify-center rounded-xl border border-rule bg-paper">
                    <span className="font-label text-[13px] font-medium uppercase tracking-[0.1em] text-ballpoint">
                      {(item.file.name.split('.').pop() || 'file').slice(0, 4)}
                    </span>
                    <span className="mt-1 font-label text-[11px] tabular-nums text-faint">
                      {readableSize(item.file.size)}
                    </span>
                  </div>
                ) : (
                  <img
                    src={item.url}
                    alt=""
                    className="h-[72px] w-[104px] rounded-xl border border-rule bg-paper object-cover"
                  />
                )}
                <button
                  type="button"
                  onClick={() => remove(item.key)}
                  className="absolute -right-2 -top-2 h-6 w-6 rounded-xl border border-rule bg-paper font-label text-[13px] leading-none text-ink"
                >
                  <span aria-hidden="true">&times;</span>
                  <span className="sr-only">Remove {item.file.name}</span>
                </button>
              </div>
              <p
                title={`${item.file.name} — ${readableSize(item.file.size)}`}
                className="mt-2 truncate font-label text-[12px] text-void"
              >
                {item.file.name}
              </p>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
