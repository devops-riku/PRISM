import { useEffect, useState } from 'react'
import { documentUrl, fetchDocument, openFile } from '../lib/api'
import { formatDate, formatMoney } from '../lib/format'
import MarkdownView from './MarkdownView'
import { ACTION, CARD, DISPLAY, MONO_LABEL } from './tokens'
import type { MouseEvent } from 'react'
import type { ProposalDocument } from '../types'

/**
 * One built proposal, on its own page.
 *
 * The strip above the document says where each part of it came from, because
 * that is the thing a studio needs to trust before sending it: the argument is
 * written, the figures are printed from a quotation that exists, and the terms
 * are the studio's own words as they stood on the day it was built.
 */

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

type ProposalViewProps = {
  documentId: string
}

export default function ProposalView({ documentId }: ProposalViewProps) {
  const [document, setDocument] = useState<ProposalDocument | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    setDocument(null)
    setError('')
    fetchDocument(documentId)
      .then((found) => live && setDocument(found))
      .catch((failure) => live && setError(failure?.message || 'That proposal did not load.'))
    return () => {
      live = false
    }
  }, [documentId])

  if (error) {
    return (
      <div className={`${CARD} px-6 py-6`}>
        <p className="font-label text-[12px] uppercase tracking-[0.14em] text-alert">Not loaded</p>
        <p className="mt-2 font-body text-[15px]">{error}</p>
      </div>
    )
  }

  if (!document) {
    return (
      <p className="px-4 py-14 text-center font-label text-[12px] uppercase tracking-[0.14em] text-faint">
        Retrieving proposal
      </p>
    )
  }

  const markdown = (document.files || []).find((file) => file.markdown)?.markdown || ''

  // What the file is called once it is on somebody's disk. The project rather
  // than the id, because a downloads folder is read by a person.
  const stem =
    (document.project_name || document.title || 'proposal')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 48) || 'proposal'

  return (
    <div className="no-scrollbar flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto">
      <section className={`${CARD} shrink-0 px-5 py-4 sm:px-6`}>
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
          <div className="min-w-[18rem]">
            <h2 className={`${DISPLAY} text-[20px]`}>
              {document.title || document.project_name || 'Proposal'}
            </h2>
            <p className={`${MONO_LABEL} mt-1`}>
              {[
                document.client_name,
                document.quotation_ref,
                formatDate(document.created_at, { withTime: true }),
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </div>

          <span className="flex flex-wrap items-center gap-2">
            <span className="font-label text-[15px] tabular-nums text-ink">
              {formatMoney(document.total, document.currency)}
            </span>
            <a
              className={ACTION}
              href={documentUrl(document.id, 'pdf')}
              download={`${stem}-proposal.pdf`}
              onClick={take(documentUrl(document.id, 'pdf'), `${stem}-proposal.pdf`)}
            >
              PDF
            </a>
            <a
              className={ACTION}
              href={documentUrl(document.id, 'html')}
              target="_blank"
              rel="noreferrer"
              onClick={take(documentUrl(document.id, 'html'))}
            >
              Print
            </a>
            <a
              className={ACTION}
              href={documentUrl(document.id, 'md')}
              download={`${stem}-proposal.md`}
              onClick={take(documentUrl(document.id, 'md'), `${stem}-proposal.md`)}
            >
              Markdown
            </a>
          </span>
        </div>

        {/* Where each part of the document came from. The one thing worth
            knowing before sending it to a client. */}
        <dl className="mt-4 grid grid-cols-1 gap-3 border-t border-hairline pt-3 sm:grid-cols-3">
          <div>
            <dt className={MONO_LABEL}>The argument</dt>
            <dd className="mt-1 font-body text-[13px] leading-[1.6] text-void">
              Written for this quotation. No figures, no terms.
            </dd>
          </div>
          <div>
            <dt className={MONO_LABEL}>The figures</dt>
            <dd className="mt-1 font-body text-[13px] leading-[1.6] text-void">
              Printed from{' '}
              <a href={`#/q/${document.quotation_id}`} className="text-ballpoint">
                {document.quotation_ref || document.quotation_id}
              </a>
              , not restated.
            </dd>
          </div>
          <div>
            <dt className={MONO_LABEL}>The terms</dt>
            <dd className="mt-1 font-body text-[13px] leading-[1.6] text-void">
              {document.policies?.length || 0} of your clauses, word for word as they stood on the
              day this was built.
            </dd>
          </div>
        </dl>
      </section>

      <article className="sheet shrink-0 p-6 sm:p-10">
        <MarkdownView markdown={markdown} tone="original" />
      </article>
    </div>
  )
}
