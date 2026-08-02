import { openFile } from '../lib/api'
import { ACTION } from './tokens'
import type { MouseEvent } from 'react'

/**
 * Fetch a file with the session in a header, rather than letting the browser
 * follow the link on its own.
 *
 * The href stays real - it is worth being able to see and copy - but a plain
 * navigation cannot carry an Authorization header, and putting the token in the
 * address instead would write a live session into the API's access log and the
 * browser's history. So the click is taken here and the file arrives as a blob.
 *
 * Modifier-clicks are left alone: somebody opening in a new tab on an install
 * with no accounts gets exactly what they asked for.
 */
function take(url: string, download = ''): (event: MouseEvent<HTMLAnchorElement>) => void {
  return (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
    event.preventDefault()
    // `failure` takes its type from `Promise.catch`, which the standard library
    // declares as `any`. Narrowing it to `unknown` would force `?.message` to be
    // rewritten, and every rewrite of it changes what a falsy message falls back to.
    openFile(url, { download }).catch((failure) => {
      window.alert(failure?.message || 'That file could not be opened.')
    })
  }
}


type SheetHeaderProps = {
  label: string
  filename: string
  downloadHref: string
  printHref: string
  pdfHref: string
  headingId: string
}

/**
 * The header strip of a sheet: which copy this is, the filename it saves as,
 * and the two things you can do with it.
 */
export default function SheetHeader({
  label,
  filename,
  downloadHref,
  printHref,
  pdfHref,
  headingId,
}: SheetHeaderProps) {
  const pdfName = String(filename || "document.md").replace(/.md$/, ".pdf")
  return (
    <header className="mb-10 flex flex-wrap items-end justify-between gap-x-8 gap-y-4 border-b border-rule pb-5">
      <div>
        <h2
          id={headingId}
          className="font-label text-[13px] font-medium uppercase tracking-[0.14em] text-ink"
        >
          {label}
        </h2>
        <p className="mt-2 font-label text-[12px] uppercase tracking-[0.14em] text-void">
          {filename}
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <a href={pdfHref} download={pdfName} className={ACTION} onClick={take(pdfHref, pdfName)}>
          Download PDF
        </a>
        <a
          href={downloadHref}
          download={filename}
          className={ACTION}
          onClick={take(downloadHref, filename)}
        >
          .md
        </a>
        <a
          href={printHref}
          target="_blank"
          rel="noopener noreferrer"
          className={ACTION}
          onClick={take(printHref)}
        >
          Print
          <span className="sr-only"> (opens the printable copy in a new tab)</span>
        </a>
      </div>
    </header>
  )
}
