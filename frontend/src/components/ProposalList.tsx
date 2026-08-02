import { useEffect, useMemo, useState } from 'react'
import { deleteDocument, documentUrl, listDocuments, openFile } from '../lib/api'
import { formatDate, formatMoney } from '../lib/format'
import RowMenu from './RowMenu'
import { useRole } from '../lib/role'
import { ACTION, DISPLAY, MONO_LABEL, WELL } from './tokens'
import type { ProposalDocumentSummary } from '../types'

/**
 * Every proposal already built, newest first.
 *
 * It used to sit under the builder, which meant the list you read most grew
 * under the form you use least, and the screen got longer every time a proposal
 * was built. Finding one you sent is its own job, so it is its own page - the
 * same split the quotation list already has.
 *
 * The search is done here rather than by the server: the list endpoint answers
 * with summaries and there are only ever a few hundred, so a round trip per
 * keystroke would buy nothing.
 */

/**
 * All these catch blocks read off a rejection. A promise's reason is untyped by
 * the platform, so this is not a checked fact — it is the shape the code has
 * always assumed, written down and kept to one property.
 */
type Failure = { message?: string } | null | undefined

export default function ProposalList() {
  const { isAdmin } = useRole()
  const [rows, setRows] = useState<ProposalDocumentSummary[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmingId, setConfirmingId] = useState('')

  useEffect(() => {
    let live = true
    listDocuments()
      .then((found) => {
        if (live) setRows(found)
      })
      .catch((failure: Failure) => {
        if (live) setError(failure?.message || 'The proposal list did not load.')
      })
      .finally(() => {
        if (live) setLoading(false)
      })
    return () => {
      live = false
    }
  }, [])

  const found = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return rows
    return rows.filter((row) =>
      [row.title, row.project_name, row.client_name, row.quotation_ref, row.id]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(needle)),
    )
  }, [rows, query])

  const handleDelete = (id: string) => {
    deleteDocument(id)
      .then(() => {
        setConfirmingId('')
        setRows((current) => current.filter((row) => row.id !== id))
      })
      .catch((failure: Failure) => setError(failure?.message || 'That proposal was not deleted.'))
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <section className="flex min-h-0 flex-1 flex-col rounded-xl border border-rule bg-paper shadow-sheet">
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-rule px-5 py-3 sm:px-6">
          <div className="flex items-baseline gap-3">
            <h2 className={`${DISPLAY} text-[20px]`}>Proposals</h2>
            <p className="font-label text-[12px] uppercase tracking-[0.14em] tabular-nums text-faint">
              {loading ? 'Reading' : `${found.length} built`}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <label htmlFor="proposal_search" className="sr-only">
              Find a proposal
            </label>
            <input
              id="proposal_search"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Project, client or reference"
              className={`${WELL} w-[260px] py-2`}
            />
          </div>
        </div>

        {error ? (
          <p role="alert" className="border-b border-rule px-5 py-4 font-body text-[15px] sm:px-6">
            <span className="font-label text-[12px] uppercase tracking-[0.14em] text-alert">
              Not loaded
            </span>
            <span className="mt-1 block">{error}</span>
          </p>
        ) : null}

        {!loading && found.length === 0 && !error ? (
          <p className="px-5 py-12 text-center font-body text-[15px] text-void sm:px-6">
            {query ? (
              `Nothing built matches “${query}”.`
            ) : (
              <>
                No proposals yet.{' '}
                <a href="#/proposals" className="text-ballpoint underline underline-offset-[3px]">
                  Build one from a quotation
                </a>
                .
              </>
            )}
          </p>
        ) : null}

        {found.length > 0 ? (
          <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto pb-2">
            {found.map((row) => (
              <article
                key={row.id}
                className="row-touch flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-hairline px-5 py-3 last:border-b-0 sm:px-6"
              >
                <div className="min-w-[16rem] flex-1">
                  <a
                    href={`#/p/${row.id}`}
                    className="font-body text-[15px] text-ink no-underline hover:text-ballpoint"
                  >
                    {row.title || row.project_name || 'Untitled'}
                  </a>
                  <p className={`${MONO_LABEL} mt-1`}>
                    {[
                      // Its own number first - that is what this document is
                      // called - then the quotation it was priced from.
                      row.reference,
                      row.client_name,
                      row.quotation_ref,
                      `${row.policy_count} clauses`,
                      formatDate(row.created_at, { withTime: true }),
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                </div>

                <span className="font-label text-[13px] tabular-nums text-ink">
                  {formatMoney(row.total, row.currency)}
                </span>

                {confirmingId === row.id ? (
                  <span className="inline-flex gap-2">
                    <button
                      type="button"
                      className={`${ACTION} border-alert text-alert`}
                      onClick={() => handleDelete(row.id)}
                    >
                      Delete for good
                    </button>
                    <button type="button" className={ACTION} onClick={() => setConfirmingId('')}>
                      Keep
                    </button>
                  </span>
                ) : (
                  <RowMenu
                    label={`Actions for proposal ${row.id}`}
                    items={[
                      { label: 'Open', href: `#/p/${row.id}` },
                      {
                        label: 'Download PDF',
                        onSelect: () =>
                          openFile(documentUrl(row.id, 'pdf'), {
                            download: `${row.id}-proposal.pdf`,
                          }).catch((failure: Failure) => setError(failure?.message || 'That file did not open.')),
                      },
                      {
                        label: 'Download markdown',
                        onSelect: () =>
                          openFile(documentUrl(row.id, 'md'), {
                            download: `${row.id}-proposal.md`,
                          }).catch((failure: Failure) => setError(failure?.message || 'That file did not open.')),
                      },
                      { label: 'The quotation', href: `#/q/${row.quotation_id}` },
                      ...(isAdmin
                        ? [
                            {
                              label: 'Delete',
                              danger: true,
                              onSelect: () => setConfirmingId(row.id),
                            },
                          ]
                        : []),
                    ]}
                  />
                )}
              </article>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  )
}
