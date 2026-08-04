import { useCallback, useEffect, useMemo, useState } from 'react'
import { deleteProposal, listProposals } from '../lib/api'
import { formatDate, formatMoney } from '../lib/format'
import RowMenu from './RowMenu'
import { useRole } from '../lib/role'
import { ACTION, DISPLAY, WELL } from './tokens'
import type { ProposalSummary } from '../types'

/**
 * The book of quotations: everything ever prepared, newest first.
 *
 * Split out from the settings panel because the two are different jobs done at
 * different times. This is the one you open every day to find what you sent
 * somebody; settings is the one you open twice a year. Sharing a screen made
 * the common task scroll past the rare one.
 *
 * Rows are summaries, never whole bundles — a bundle carries both rendered
 * documents and runs to tens of kilobytes.
 */

/**
 * All these catch blocks read off a rejection. A promise's reason is untyped by
 * the platform, so this is not a checked fact — it is the shape the code has
 * always assumed, written down and kept to one property.
 */
type Failure = { message?: string } | null | undefined

type QuotationListProps = {
  onOpen?: (id: string) => void
}

export default function QuotationList({ onOpen }: QuotationListProps) {
  const { isAdmin } = useRole()
  const [rows, setRows] = useState<ProposalSummary[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmingId, setConfirmingId] = useState('')
  // A filter, not a second search: the field finds one quotation, these narrow
  // the whole list to a kind of quotation. Kept to the two facts that change
  // what somebody does next - whether a target was met, and which currency it
  // was quoted in.
  const [filter, setFilter] = useState('all')
  const [copied, setCopied] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    return listProposals({ q: query })
      .then((found) => setRows(found))
      .catch((failure: Failure) => setError(failure?.message || 'The quotation list did not load.'))
      .finally(() => setLoading(false))
  }, [query])

  useEffect(() => {
    load()
  }, [load])

  const currencies = useMemo(() => {
    const seen: string[] = []
    rows.forEach((row) => {
      if (row.currency && !seen.includes(row.currency)) seen.push(row.currency)
    })
    return seen.slice(0, 4)
  }, [rows])

  const shown = useMemo(() => {
    if (filter === 'all') return rows
    if (filter === 'target-met') return rows.filter((row) => row.target_total > 0 && row.hit_target)
    if (filter === 'target-missed')
      return rows.filter((row) => row.target_total > 0 && !row.hit_target)
    return rows.filter((row) => row.currency === filter)
  }, [rows, filter])

  const copyReference = (row: ProposalSummary) => {
    const reference = row.quotation_ref || row.id
    navigator.clipboard
      ?.writeText(reference)
      .then(() => {
        setCopied(row.id)
        window.setTimeout(() => setCopied(''), 1400)
      })
      .catch(() => setError('That reference could not be copied.'))
  }

  const handleDelete = (id: string) => {
    setError('')
    deleteProposal(id)
      .then(() => {
        setConfirmingId('')
        setRows((current) => current.filter((row) => row.id !== id))
      })
      .catch((failure: Failure) => setError(failure?.message || 'That quotation was not deleted.'))
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <section className="flex min-h-0 flex-1 flex-col rounded-xl border border-rule bg-paper shadow-sheet">
        {/* Title and search on one line. They were stacked bands, which spent a
            third of the screen before the first row of the thing you came to
            look at. */}
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-rule px-5 py-3 sm:px-6">
          <div className="flex items-baseline gap-3">
            <h2 className={`${DISPLAY} text-[20px]`}>PAD Quotations</h2>
            <p className="font-label text-[12px] uppercase tracking-[0.14em] tabular-nums text-faint">
              {loading ? 'Reading' : `${shown.length} of ${rows.length}`}
            </p>
          </div>

          {/* No running total here. Summing quotations that were never sent,
              were superseded, or are three revisions of one job produces a
              figure that means nothing - and a number that large beside a
              search box reads like a pipeline it is not. */}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <label htmlFor="quotation_search" className="sr-only">
              Find a quotation
            </label>
            <input
              id="quotation_search"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Project, client or reference"
              className={`${WELL} w-[260px] py-2`}
            />
          </div>
        </div>

        {/* Chips rather than a select: four options that each answer "show me
            fewer" are quicker to press than to open. */}
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-hairline px-5 py-2.5 sm:px-6">
          {[
            { id: 'all', label: `All ${rows.length}` },
            { id: 'target-met', label: 'Target met' },
            { id: 'target-missed', label: 'Target missed' },
            ...currencies.map((code) => ({ id: code, label: code })),
          ].map((option) => {
            const on = filter === option.id
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={on}
                onClick={() => setFilter(on && option.id !== 'all' ? 'all' : option.id)}
                className={`chip transition-colors duration-150 ${
                  on ? 'chip--live' : 'hover:border-ballpoint hover:text-ink'
                }`}
              >
                {option.label}
              </button>
            )
          })}
        </div>

        {/* Same left edge as the header and chips above: px-5 … sm:px-6, not
            the px-6/sm:px-8 this alert and both empty states used to carry. */}
        {error ? (
          <p role="alert" className="border-b border-rule px-5 py-4 font-body text-[15px] sm:px-6">
            <span className="font-label text-[12px] uppercase tracking-[0.14em] text-alert">
              Not loaded
            </span>
            <span className="mt-1 block">{error}</span>
          </p>
        ) : null}

        {!loading && rows.length > 0 && shown.length === 0 ? (
          <p className="px-5 py-12 text-center font-body text-[15px] text-void sm:px-6">
            Nothing on file matches that filter.
          </p>
        ) : null}

        {!loading && rows.length === 0 && !error ? (
          <p className="px-5 py-12 text-center font-body text-[15px] text-void sm:px-6">
            {query
              ? `Nothing on file matches “${query}”.`
              : 'Nothing quoted yet. Prepare a quotation and it will be filed here.'}
          </p>
        ) : null}

        {shown.length > 0 ? (
          <div className="no-scrollbar min-h-0 flex-1 overflow-auto pb-3">
            <table className="w-full border-collapse text-[13px]">
              {/* The header stays put while the rows move under it: a table you
                  scroll without column names is a table you have to scroll back
                  up to read. */}
              <thead className="sticky top-0 z-10 bg-paper">
                <tr>
                  {['Reference', 'Project', 'Client', 'Basis', 'Total', ''].map((head, index) => (
                    <th
                      key={head || `blank-${index}`}
                      scope="col"
                      className={`whitespace-nowrap border-b-2 border-ink px-4 py-2 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 font-label text-[12px] font-medium uppercase tracking-[0.14em] text-faint ${
                        head === 'Total' ? 'text-right' : 'text-left'
                      }`}
                    >
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <tr key={row.id} className="row-touch border-b border-rule last:border-b-0">
                    <td className="whitespace-nowrap px-4 py-2 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 align-top">
                      {/* The printed reference, not the storage id: this is the
                          number on the document and the one a client quotes back
                          at you. The id is still what the link resolves. */}
                      <span className="inline-flex items-center gap-1.5">
                        <a
                          href={`#/q/${row.id}`}
                          onClick={() => onOpen && onOpen(row.id)}
                          className="font-label tabular-nums text-ballpoint underline underline-offset-[3px]"
                        >
                          {row.quotation_ref || row.id}
                        </a>
                        {/* The reference is the thing a client reads back at
                            you on the phone, so it is one click to have it. */}
                        <button
                          type="button"
                          aria-label={`Copy ${row.quotation_ref || row.id}`}
                          title="Copy the reference"
                          onClick={() => copyReference(row)}
                          className="rounded-xs px-1 font-label text-[11px] uppercase tracking-[0.1em] text-faint hover:text-ballpoint"
                        >
                          {copied === row.id ? 'Copied' : 'Copy'}
                        </button>
                      </span>
                      <span className="mt-1 block font-label text-[12px] tabular-nums text-void">
                        {formatDate(row.created_at)}
                        {row.revision > 1 ? ` · rev ${row.revision}` : ''}
                      </span>
                    </td>
                    <td className="max-w-[260px] px-4 py-2 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 align-top font-body">
                      {row.project_name || <span className="text-void">Untitled</span>}
                      <span className="mt-1 block font-label text-[12px] text-void">
                        {row.line_items} line items
                      </span>
                    </td>
                    <td className="max-w-[200px] px-4 py-2 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 align-top font-body">
                      {row.client_name || <span className="text-void">—</span>}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 align-top font-label text-[12px] text-void">
                      {row.tax_label
                        ? `${row.tax_label} ${row.tax_pct}% ${row.tax_inclusive ? 'incl.' : 'excl.'}`
                        : 'No tax'}
                      {row.target_total > 0 ? (
                        <span className="mt-1 block tabular-nums">
                          {row.hit_target ? 'Target met' : 'Target missed'}
                        </span>
                      ) : null}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 text-right align-top font-label tabular-nums text-ink">
                      {formatMoney(row.total, row.currency)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 text-right align-top">
                      {/* The confirmation replaces the menu rather than opening
                          inside it: a destructive answer belongs on the row it
                          will empty, not on a surface that closes when you
                          click anywhere else. */}
                      {confirmingId === row.id ? (
                        <span className="inline-flex justify-end gap-2">
                          <button
                            type="button"
                            className={`${ACTION} border-alert text-alert`}
                            onClick={() => handleDelete(row.id)}
                          >
                            Delete for good
                          </button>
                          <button
                            type="button"
                            className={ACTION}
                            onClick={() => setConfirmingId('')}
                          >
                            Keep
                          </button>
                        </span>
                      ) : (
                        <RowMenu
                          label={`Actions for quotation ${row.id}`}
                          items={[
                            { label: 'Open', href: `#/q/${row.id}` },
                            { label: 'Build a proposal', href: '#/proposals' },
                            {
                              label: 'Download PDF',
                              href: row.proposal_url.replace(/\.md$/, '.pdf'),
                            },
                            { label: 'Download markdown', href: row.proposal_url },
                            // Deleting is an admin's. The server refuses a
                            // member either way; not offering it is the
                            // difference between a locked door and a broken one.
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
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  )
}
