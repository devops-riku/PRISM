import { Dialog, DialogPanel } from '@headlessui/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type * as React from 'react'
import { listDocuments, listProposals } from '../lib/api'
import { useRole } from '../lib/role'
import { formatMoney } from '../lib/format'
import type { ProposalDocumentSummary, ProposalSummary } from '../types'

/**
 * One field that reaches everything, so no screen needs a menu.
 *
 * PRISM took its navigation strip down some time ago and put five cards on the
 * front page instead. That is a good landing, and a bad way to get from a
 * quotation you are reading to a proposal you built last week — which is the
 * move somebody actually makes twenty times a day.
 *
 * So: ⌘K anywhere. It searches what the studio has, not what the app can do —
 * quotations by reference, project or client, and proposals by title — with the
 * screens listed underneath. Results come from the same endpoints the list
 * pages use, so what it finds is what is on file rather than a second index
 * that can drift.
 */

const PLACES = [
  { id: 'pad', label: 'Create PAD', hint: 'New quotation', href: '#/pad' },
  { id: 'quotations', label: 'PAD Quotations', hint: 'Everything quoted', href: '#/quotations' },
  { id: 'build', label: 'Build Proposal', hint: 'From a quotation', href: '#/proposals' },
  { id: 'proposals', label: 'Proposals', hint: 'Everything built', href: '#/documents' },
  { id: 'jobs', label: 'In progress', hint: 'Work running now', href: '#/jobs' },
  { id: 'settings', label: 'Settings', hint: 'Rates, terms, design', href: '#/settings' },
  { id: 'workspaces', label: 'Workspaces', hint: 'Switch or make one', href: '#/workspaces' },
  { id: 'teams', label: 'Teams', hint: 'Who is on this workspace', href: '#/teams' },
  { id: 'profile', label: 'Profile', hint: 'The account you are in as', href: '#/profile' },
]

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '')

/** What the two list endpoints last handed back. */
type CommandRows = {
  quotations: ProposalSummary[]
  proposals: ProposalDocumentSummary[]
}

/** One row of the bar: a quotation, a proposal, or a screen to go to. */
type CommandItem = {
  key: string
  kind: string
  title: string
  detail: string
  aside: string
  href: string
}

export default function CommandBar() {
  const { isAdmin } = useRole()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<CommandRows>({ quotations: [], proposals: [] })
  const [cursor, setCursor] = useState(0)
  const field = useRef<HTMLInputElement | null>(null)

  // ⌘K on a Mac, Ctrl-K everywhere else. Escape closes, which Dialog handles.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen((was) => !was)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // What is on file, read when the bar opens and again as the query settles.
  // Debounced, because a keystroke is not a reason to ask the server twice.
  useEffect(() => {
    if (!open) return undefined
    let live = true
    const timer = window.setTimeout(() => {
      Promise.all([
        listProposals({ q: query, limit: 8 }).catch(() => []),
        listDocuments().catch(() => []),
      ]).then(([quotations, proposals]) => {
        if (!live) return
        setRows({ quotations: quotations.slice(0, 6), proposals })
      })
    }, query ? 160 : 0)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [open, query])

  const needle = query.trim().toLowerCase()

  const items = useMemo(() => {
    const found: CommandItem[] = []

    rows.quotations.forEach((row) =>
      found.push({
        key: `q-${row.id}`,
        kind: 'Quotation',
        title: row.project_name || 'Untitled',
        detail: [row.quotation_ref || row.id, row.client_name].filter(Boolean).join(' · '),
        aside: formatMoney(row.total, row.currency),
        href: `#/q/${row.id}`,
      }),
    )

    rows.proposals
      .filter((row) =>
        !needle
          ? true
          : [row.title, row.project_name, row.client_name, row.quotation_ref]
              .filter(Boolean)
              .some((field) => String(field).toLowerCase().includes(needle)),
      )
      .slice(0, 5)
      .forEach((row) =>
        found.push({
          key: `p-${row.id}`,
          kind: 'Proposal',
          title: row.title || row.project_name || 'Untitled',
          detail: [row.client_name, row.quotation_ref].filter(Boolean).join(' · '),
          aside: formatMoney(row.total, row.currency),
          href: `#/p/${row.id}`,
        }),
      )

    PLACES.filter(
      (place) =>
        (isAdmin || place.id !== 'settings') &&
        (!needle ||
        place.label.toLowerCase().includes(needle) ||
          place.hint.toLowerCase().includes(needle)),
    ).forEach((place) =>
      found.push({
        key: `go-${place.id}`,
        kind: 'Go to',
        title: place.label,
        detail: place.hint,
        aside: '',
        href: place.href,
      }),
    )

    return found
  }, [rows, needle, isAdmin])

  useEffect(() => setCursor(0), [needle, open])

  const go = useCallback((item: CommandItem | undefined) => {
    if (!item) return
    setOpen(false)
    setQuery('')
    window.location.hash = item.href
  }, [])

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setCursor((at) => Math.min(at + 1, items.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setCursor((at) => Math.max(at - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      go(items[cursor])
    }
  }

  return (
    <>
      {/* On every screen, the front page included. The cards are one way
          through the app and this is the other; hiding it on the page somebody
          lands on made the faster route the one they had to already know
          about. */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="hidden items-center gap-3 rounded-[13px] border border-rule bg-duplicate py-1.5 pl-3 pr-2 text-left transition-colors duration-150 hover:border-ballpoint hover:bg-paper sm:flex"
        aria-label="Quick navigation: quotations, proposals and screens"
      >
        <span aria-hidden="true" className="text-[13px] text-ballpoint">
          ⌕
        </span>
        <span className="font-body text-[13px] text-faint">Quick navigation</span>
        <span className="flex gap-1">
          <kbd className="kbd">{MAC ? '⌘' : 'Ctrl'}</kbd>
          <kbd className="kbd">K</kbd>
        </span>
      </button>

      <Dialog open={open} onClose={() => setOpen(false)} className="relative z-[60]">
        <div className="fixed inset-0 bg-ink/25 backdrop-blur-[2px]" aria-hidden="true" />
        <div className="fixed inset-0 flex items-start justify-center px-4 pt-[12vh]">
          <DialogPanel className="w-full max-w-[34rem] overflow-hidden rounded-[18px] border border-rule bg-paper shadow-raised">
            <div className="flex items-center gap-3 border-b border-hairline px-4 py-3">
              <span aria-hidden="true" className="text-[15px] text-ballpoint">
                ⌕
              </span>
              <input
                ref={field}
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Quick navigation — type a project, client or screen"
                className="w-full bg-transparent font-body text-[15px] text-ink placeholder:text-faint focus:outline-none"
              />
              <kbd className="kbd">esc</kbd>
            </div>

            <div className="no-scrollbar max-h-[46vh] overflow-y-auto py-1">
              {items.length === 0 ? (
                <p className="px-4 py-8 text-center font-body text-[14px] text-void">
                  Nothing matches “{query}”.
                </p>
              ) : null}

              {items.map((item, index) => (
                <button
                  key={item.key}
                  type="button"
                  onMouseEnter={() => setCursor(index)}
                  onClick={() => go(item)}
                  className={`flex w-full items-center gap-3 px-4 py-2.5 text-left ${
                    index === cursor ? 'bg-accent-soft' : ''
                  }`}
                >
                  {/* 84px, and `whitespace-nowrap` regardless.
                      Measured in the real Instrument Sans at 11px with this
                      tracking: QUOTATION is 75.5px, PROPOSAL 70.2, GO TO 41.1.
                      The column was 74 - a pixel and a half short of the
                      longest of the three, so the widest label broke to
                      "QUOTATIO / N" and took the row's height with it.
                      The fixed width is what lines the titles up, so it stays
                      fixed rather than becoming `min-w`; `nowrap` is the belt
                      to its braces, so a label added later overflows visibly
                      instead of silently wrapping and misaligning every row
                      after it. */}
                  <span className="w-[84px] shrink-0 whitespace-nowrap font-label text-[11px] uppercase tracking-[0.12em] text-faint">
                    {item.kind}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-body text-[14px] text-ink">
                      {item.title}
                    </span>
                    {item.detail ? (
                      <span className="mt-0.5 block truncate font-label text-[11.5px] uppercase tracking-[0.1em] text-faint">
                        {item.detail}
                      </span>
                    ) : null}
                  </span>
                  {item.aside ? (
                    <span className="shrink-0 font-label text-[12.5px] tabular-nums text-void">
                      {item.aside}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-3 border-t border-hairline px-4 py-2">
              <span className="flex items-center gap-1.5 font-label text-[11px] uppercase tracking-[0.12em] text-faint">
                <kbd className="kbd">↑</kbd>
                <kbd className="kbd">↓</kbd>
                move
              </span>
              <span className="flex items-center gap-1.5 font-label text-[11px] uppercase tracking-[0.12em] text-faint">
                <kbd className="kbd">↵</kbd>
                open
              </span>
            </div>
          </DialogPanel>
        </div>
      </Dialog>
    </>
  )
}
