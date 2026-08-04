import type { MouseEvent } from 'react'
import { openFile } from './api'
import { INLINE_KINDS } from '../types'

/**
 * Open one of a client's own files with the session in a header, rather than
 * letting the browser follow the link on its own.
 *
 * The same shape `ProposalView.tsx`'s and `SheetHeader.tsx`'s own `take`
 * helpers already are. The href stays real underneath - worth being able to
 * copy or open in a new tab - but a plain navigation cannot carry the
 * `Authorization` header this route needs once accounts are configured, so the
 * click is taken here and the file arrives as a blob, exactly as every other
 * authed file in this app already does.
 *
 * `download` mirrors the server's own `Content-Disposition` choice rather than
 * forcing a save regardless of kind: a raster opens in a new tab, which is what
 * `inline` on the response is for, and a document downloads, matching
 * `attachment`. `openFile` never reads that header itself - it cannot, the
 * header describes bytes already in a blob by the time this runs - so this is a
 * second, independent read of the same fact from the one place the frontend has
 * it: the manifest's own `kind`, against `INLINE_KINDS`.
 *
 * IN ITS OWN FILE because two screens need it. It lived in
 * `IntakeListScreen.tsx` while the queue was the only place a client's file
 * could be opened; the pad now shows the same files, and the alternative was
 * either importing a helper out of a screen or writing the inline-vs-download
 * rule down a second time. The second of those is what this codebase has
 * already been bitten by twice with the raster allowlist, so: one statement,
 * one file, two callers.
 */
export function openAttachment(
  url: string,
  name: string,
  kind: string,
): (event: MouseEvent<HTMLAnchorElement>) => void {
  const download = !INLINE_KINDS.has(kind)
  return (event) => {
    // Ctrl/Cmd/Shift-click and middle-click are the browser's to answer, not
    // this app's - taking them would break "open in a new tab" on a link whose
    // href is real precisely so that works.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
    event.preventDefault()
    openFile(url, download ? { download: name } : {}).catch((failure) => {
      window.alert(failure?.message || 'That file could not be opened.')
    })
  }
}
