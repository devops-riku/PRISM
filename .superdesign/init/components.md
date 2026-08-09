# Shared UI components

PRISM's frontend is React 18 on Vite. It uses Tailwind CSS v4 utilities, project-defined classes and CSS variables, and Headless UI for accessible unstyled behavior. There is no third-party visual component library. The files below are the bounded reusable layer most useful to design work; page/screen compositions live in `layouts.md`.

Each source is included once and in full. Shared class strings from `frontend/src/components/tokens.ts` and global CSS tokens are intentionally left to `theme.md` to avoid duplicating design-system source.

## PrismMark

- File: `frontend/src/components/PrismMark.tsx`
- Description: Theme-aware PRISM SVG mark with a fixed three-color spectrum.
- Key props: `size?: number`, `title?: string`, `className?: string`

```tsx
/**
 * The app's mark: a prism drawn as an outlined triangle on a rounded tile,
 * with the spectrum it produces as three dashes beneath it.
 *
 * ONE DEFINITION, used everywhere in the React app - the landing page, the
 * sign-in card, and anywhere else a logo is wanted. The two copies that
 * cannot import it are `index.html`'s favicon and its boot splash, because
 * both paint before this bundle exists; each says so in its own comment and
 * names this file.
 *
 * The tile and the triangle take `--logo-tile` and `--logo-mark`, so the mark
 * follows the theme. THE THREE DASHES NEVER DO. They are the spectrum, they
 * are the reason the product is called PRISM, and a prism that splits white
 * light into three shades of the same colour is not a prism. They are brand,
 * not palette, and no colour sweep should touch them.
 */

/** The spectrum, in the order light leaves the glass. */
const SPECTRUM = ['#1b98a8', '#e3ae3c', '#d9645e'] as const

type PrismMarkProps = {
  /** Rendered size in px. The artwork is a square, so one number does. */
  size?: number
  /** A mark inside a labelled control is decoration; standalone it is not. */
  title?: string
  className?: string
}

export default function PrismMark({ size = 32, title, className = '' }: PrismMarkProps) {
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={`flex-none ${className}`}
      role={title ? 'img' : undefined}
      aria-label={title || undefined}
      aria-hidden={title ? undefined : true}
    >
      <rect width="64" height="64" rx="18" fill="var(--logo-tile)" />
      {/* Apex up, corners mitred rather than rounded off - the artwork's own
          joins are square, and rounding them turns the prism into a tent. */}
      <path
        d="M32 15.5 L47.5 43 H16.5 Z"
        fill="none"
        stroke="var(--logo-mark)"
        strokeWidth="3.1"
        strokeLinejoin="round"
      />
      {SPECTRUM.map((colour, index) => (
        <line
          key={colour}
          x1={18.5 + index * 9.6}
          y1="51.5"
          x2={25.5 + index * 9.6}
          y2="51.5"
          stroke={colour}
          strokeWidth="2.8"
          strokeLinecap="round"
        />
      ))}
    </svg>
  )
}

```

## CommandBar

- File: `frontend/src/components/CommandBar.tsx`
- Description: Global Ctrl/⌘-K search and navigation control used by the authenticated header.
- Key props: No external props; open/query/cursor state is internal.

```tsx
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
        className="hidden items-center gap-3 rounded-[13px] border border-rule bg-duplicate py-1.5 pl-3 pr-2 text-left transition-colors duration-150 hover:bg-paper sm:flex"
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

```

## NotificationBell

- File: `frontend/src/components/NotificationBell.tsx`
- Description: Global notification trigger, unread badge, and anchored notification panel.
- Key props: No external props; notification data and open state are internal.

```tsx
import { Popover, PopoverButton, PopoverPanel } from '@headlessui/react'
import { useEffect, useRef, useState } from 'react'
import { clearSeen, markRead, useNotifications } from '../lib/notifications'
import { formatDate } from '../lib/format'
import { MONO_LABEL } from './tokens'

/**
 * What happened while you were not looking.
 *
 * The count is the whole feature: a bell with nothing on it should be silent
 * furniture, and one with a number on it should be worth opening. So the server
 * only ever writes notes somebody has to act on or would want to know — work
 * that landed, work that failed, and changes to the team or to what the studio
 * charges. Progress, saves and "you did this a second ago" are deliberately not
 * here.
 *
 * Read is marked when the panel CLOSES, not when it opens, so the rows you came
 * to look at are still marked new while you are looking at them.
 */

const TONE: Record<string, string> = {
  quotation_failed: 'alert',
  quotation_rejected: 'alert',
  quotation_crashed: 'alert',
  revision_failed: 'alert',
  proposal_failed: 'alert',
  generation_blocked: 'alert',
  work_lost_to_restart: 'alert',
  removed_from_team: 'alert',
  'intake.quote_failed': 'alert',
}

type DotProps = {
  kind: string
  unread: boolean
}

function Dot({ kind, unread }: DotProps) {
  const alert = TONE[kind] === 'alert'
  return (
    <span
      aria-hidden="true"
      className={`mt-[7px] h-1.5 w-1.5 flex-none rounded-full ${
        alert ? 'bg-alert' : unread ? 'bg-ballpoint' : 'bg-rule'
      }`}
    />
  )
}

export default function NotificationBell() {
  const { unread, notes } = useNotifications()
  const [open, setOpen] = useState(false)
  const wasOpen = useRef(false)

  // Marked read in an effect, not during render: render runs for reasons that
  // have nothing to do with this panel - a poll landing, a parent updating -
  // and a write fired from render would go off at all of them.
  useEffect(() => {
    if (wasOpen.current && !open) markRead()
    wasOpen.current = open
  }, [open])

  return (
    <Popover className="relative">
      {({ open: isOpen, close }) => {
        if (isOpen !== open) setOpen(isOpen)

        return (
          <>
            <PopoverButton
              aria-label={
                unread ? `Notifications, ${unread} unread` : 'Notifications, nothing new'
              }
              className="relative flex h-9 w-9 items-center justify-center rounded-md border border-transparent text-void transition-[background-color,border-color,color] duration-150 hover:bg-paper hover:text-ballpoint data-[open]:border-rule data-[open]:bg-paper"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-[18px] w-[18px]"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5Z" />
                <path d="M10.2 18.5a2 2 0 0 0 3.6 0" />
              </svg>
              {unread ? (
                /* Outside the icon, not over it: a count sitting on the bell
                   hid the thing it was counting. The canvas-coloured ring is
                   what keeps it legible against the icon's strokes. */
                <span className="pointer-events-none absolute -right-[3px] -top-[3px] flex h-[16px] min-w-[16px] items-center justify-center rounded-pill bg-ballpoint px-[4px] font-label text-[10px] font-medium leading-none text-paper ring-2 ring-canvas">
                  {unread > 9 ? '9+' : unread}
                </span>
              ) : null}
            </PopoverButton>

            {/* Neither modal nor transitioned, for the reason written out in
                RowMenu: one pads the document and shifts the page, the other
                shows the panel's pre-measurement position. */}
            <PopoverPanel
              anchor="bottom end"
              modal={false}
              className="z-50 w-[22rem] rounded-[18px] border border-rule bg-paper shadow-raised [--anchor-gap:8px] focus:outline-none"
            >
              <div className="flex items-baseline justify-between gap-4 border-b border-hairline px-4 py-3">
                <p className={MONO_LABEL}>Notifications</p>
                {notes.length ? (
                  <button
                    type="button"
                    onClick={() => clearSeen()}
                    className="font-body text-[12.5px] text-void hover:text-ballpoint"
                  >
                    Clear read
                  </button>
                ) : null}
              </div>

              {notes.length === 0 ? (
                <p className="px-4 py-8 text-center font-body text-[13.5px] leading-[1.6] text-void">
                  Nothing yet. When a quotation lands, a proposal is built, or somebody joins the
                  team, it turns up here.
                </p>
              ) : (
                <div className="no-scrollbar max-h-[24rem] overflow-y-auto">
                  {notes.map((note) => {
                    const body = (
                      <>
                        <Dot kind={note.kind} unread={!note.read_at} />
                        <span className="min-w-0 flex-1">
                          <span
                            className={`block font-body text-[14px] leading-[1.4] ${
                              note.read_at ? 'text-void' : 'text-ink'
                            }`}
                          >
                            {note.title}
                          </span>
                          {note.body ? (
                            <span className="mt-0.5 block font-body text-[12.5px] leading-[1.5] text-void">
                              {note.body}
                            </span>
                          ) : null}
                          <span className="mt-1 block font-label text-[11px] uppercase tracking-[0.1em] text-faint">
                            {formatDate(note.at, { withTime: true })}
                          </span>
                        </span>
                      </>
                    )

                    return note.href ? (
                      <a
                        key={note.id}
                        href={note.href}
                        // Closing on the way out does two things: it marks the
                        // panel read, and it stops the panel hanging over the
                        // page it just sent you to.
                        onClick={() => close()}
                        className="row-touch flex gap-3 border-b border-hairline px-4 py-3 no-underline last:border-b-0 hover:bg-duplicate"
                      >
                        {body}
                      </a>
                    ) : (
                      <div
                        key={note.id}
                        className="flex gap-3 border-b border-hairline px-4 py-3 last:border-b-0"
                      >
                        {body}
                      </div>
                    )
                  })}
                </div>
              )}

              <div className="border-t border-hairline px-4 py-2">
                <a
                  href="#/jobs"
                  className="font-body text-[12.5px] text-ballpoint no-underline hover:underline"
                >
                  Everything in progress
                </a>
              </div>
            </PopoverPanel>
          </>
        )
      }}
    </Popover>
  )
}

```

## WorkspaceMenu

- File: `frontend/src/components/WorkspaceMenu.tsx`
- Description: Global workspace switcher used by the authenticated header.
- Key props: No external props; active workspace and rows are loaded internally.

```tsx
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react'
import { useEffect, useState } from 'react'
import { currentWorkspace, listWorkspaces, setCurrentWorkspace } from '../lib/api'
import type { Workspace } from '../types'

/**
 * Which book of work you are looking at.
 *
 * A workspace is a whole separate studio: its own settings, rate card, terms,
 * proposal template and design, its own quotation numbering, and its own
 * quotations and proposals. Nothing crosses between them, which is why the
 * switch reloads the page rather than swapping data underneath a screen — half
 * a screen showing one studio's quotations beside another's total is the exact
 * confusion workspaces exist to prevent.
 *
 * Switching only. Making, renaming and deleting are on the workspaces page,
 * where the counts of what is in each are in front of you - a menu item is a
 * poor place to be told a delete takes 159 quotations with it.
 */

export default function WorkspaceMenu() {
  const [rows, setRows] = useState<Workspace[]>([])
  const [error, setError] = useState('')

  const chosen = currentWorkspace()
  const active = rows.find((row) => row.id === chosen) || rows[0] || null

  const load = () =>
    listWorkspaces()
      .then((found) => {
        setRows(found)
        setError('')
        // A workspace that was deleted elsewhere, or a stored id from an older
        // install: forget it rather than sending a name the server will ignore.
        if (chosen && !found.some((row) => row.id === chosen)) setCurrentWorkspace('')
      })
      .catch((failure) => setError(failure?.message || 'The workspaces did not load.'))

  useEffect(() => {
    load()
    // Read once. Workspaces change when somebody changes them, and this menu is
    // the thing that changes them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const switchTo = (id: string) => {
    if (id === (active?.id || '')) return
    setCurrentWorkspace(id)
    // Everything on screen belongs to the workspace that was open, so the whole
    // page is reloaded rather than refetched piecemeal.
    window.location.reload()
  }

  if (!rows.length && !error) return null

  return (
    <Menu>
      <MenuButton
        aria-label="Workspace"
        className="flex items-center gap-2 rounded-md border border-transparent px-2 py-1 transition-[background-color,border-color] duration-150 hover:bg-paper data-[open]:border-rule data-[open]:bg-paper"
      >
        <span className="h-1.5 w-1.5 flex-none rounded-full bg-ballpoint" aria-hidden="true" />
        <span className="max-w-[18ch] truncate font-label text-[12px] uppercase tracking-[0.12em] text-void">
          {active ? active.name : 'Workspace'}
        </span>
        <svg
          aria-hidden="true"
          viewBox="0 0 12 8"
          className="h-2 w-3 flex-none text-faint"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M1 1.5 6 6.5 11 1.5" />
        </svg>
      </MenuButton>

      {/* Neither modal nor transitioned — same reasons as RowMenu: one pads the
          document and shifts the page, the other shows the panel's
          pre-measurement position. */}
      <MenuItems
        anchor="bottom start"
        modal={false}
        className="z-50 min-w-[17rem] rounded-lg border border-rule bg-paper p-1 shadow-raised [--anchor-gap:6px] focus:outline-none"
      >
        <p className="px-3 pb-1 pt-1.5 font-label text-[12px] uppercase tracking-[0.14em] text-faint">
          Workspaces
        </p>

        {rows.map((row) => {
          const here = active && row.id === active.id
          return (
            <MenuItem key={row.id}>
              <button
                type="button"
                onClick={() => switchTo(row.id)}
                className={`flex w-full items-baseline justify-between gap-3 rounded-xs px-3 py-2 text-left data-[focus]:bg-duplicate ${
                  here ? 'bg-accent-soft' : ''
                }`}
              >
                <span className="min-w-0">
                  <span className="block truncate font-body text-[14px] text-ink">{row.name}</span>
                  <span className="mt-0.5 block font-label text-[11px] uppercase tracking-[0.12em] text-faint">
                    {row.quotations} quotations · {row.proposals} proposals
                  </span>
                </span>
                {here ? (
                  <span className="font-label text-[11px] uppercase tracking-[0.12em] text-ballpoint">
                    Open
                  </span>
                ) : null}
              </button>
            </MenuItem>
          )
        })}

        <div role="presentation" className="my-1 h-px bg-hairline" />

        {/* Renaming and deleting live on the workspaces page, where the counts
            are in front of you. A menu is for switching. */}
        <MenuItem>
          <a
            href="#/workspaces"
            className="block rounded-xs px-3 py-2 font-body text-[14px] text-body no-underline data-[focus]:bg-duplicate"
          >
            Manage workspaces
          </a>
        </MenuItem>

        {error ? (
          <p role="alert" className="px-3 pb-2 font-body text-[12px] text-alert">
            {error}
          </p>
        ) : null}
      </MenuItems>
    </Menu>
  )
}

```

## Dropdown

- File: `frontend/src/components/Dropdown.tsx`
- Description: Generic accessible Headless UI listbox styled as a PRISM field.
- Key props: `id?`, `value`, `onChange`, `options?`, `disabled?`, `placeholder?`, `className?`, `buttonClassName?`, plus listbox-button attributes.

```tsx
import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from '@headlessui/react'
import type { ListboxButtonProps } from '@headlessui/react'

/**
 * A select that belongs to this design rather than to the operating system.
 *
 * A native `<select>` cannot be styled where it matters: the popup is drawn by
 * the OS, in the OS's font, at the OS's row height. Next to fields wearing the
 * kit's 12px radius and warm paper fill, it read as a control borrowed from
 * somewhere else — which is exactly what it was.
 *
 * Headless UI supplies the behaviour and nothing else: focus management, type
 * to select, arrow keys, escape to close, and the ARIA a listbox needs. The
 * appearance below is the kit's, the same `.well` every input already wears.
 *
 * Options are `{ value, label, hint }`. `hint` is a second line for the cases
 * where the choice needs explaining in the list rather than underneath it.
 *
 * Anything else passed - `aria-label` above all - lands on the button. A
 * `<label htmlFor>` still works because a button is a labelable element, but
 * the rows of a table have no room for visible labels and need the attribute:
 * six unit dropdowns all announcing themselves as "day" name nothing.
 */

/**
 * One row of the list. `T` is the value the field round-trips: a union of
 * literals at every call site, and constrained to `string` because a value is
 * also the option's React key.
 */
export type DropdownOption<T extends string = string> = {
  value: T
  label: string
  hint?: string
}

type DropdownOwnProps<T extends string> = {
  id?: string
  value: T
  onChange: (value: T) => void
  options?: DropdownOption<T>[]
  disabled?: boolean
  placeholder?: string
  className?: string
  buttonClassName?: string
}

/** The rest is whatever a Headless UI listbox button takes, `aria-label` above all. */
type DropdownProps<T extends string> = DropdownOwnProps<T> &
  Omit<ListboxButtonProps<'button'>, keyof DropdownOwnProps<string>>

export default function Dropdown<T extends string = string>({
  id,
  value,
  onChange,
  options = [],
  disabled = false,
  placeholder = 'Choose',
  className = '',
  buttonClassName = '',
  ...rest
}: DropdownProps<T>) {
  const selected = options.find((option) => option.value === value)

  return (
    <Listbox value={value} onChange={onChange} disabled={disabled}>
      <div className={`relative ${className}`}>
        <ListboxButton
          id={id}
          {...rest}
          className={`well flex w-full items-center justify-between gap-3 text-left data-[disabled]:cursor-not-allowed data-[disabled]:text-faint ${buttonClassName}`}
        >
          <span className={`truncate ${selected ? '' : 'text-faint'}`}>
            {selected ? selected.label : placeholder}
          </span>
          {/* Drawn, not typed: no font on Windows carries a chevron that sits
              on the baseline the way this needs to. */}
          <svg
            aria-hidden="true"
            viewBox="0 0 12 8"
            className="h-2 w-3 flex-none text-faint"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M1 1.5 6 6.5 11 1.5" />
          </svg>
        </ListboxButton>

        <ListboxOptions
          anchor="bottom start"
          // Both defaults off, for the reasons written out in RowMenu: modal
          // pads <html> and slides the page, and the fade makes the panel's
          // pre-measurement position visible as a slide.
          modal={false}
          className="z-50 w-[var(--button-width)] min-w-[12rem] rounded-lg border border-rule bg-paper p-1 shadow-raised [--anchor-gap:6px] focus:outline-none"
        >
          {options.map((option) => (
            <ListboxOption
              key={option.value}
              value={option.value}
              className="cursor-pointer rounded-xs px-3 py-2 font-body text-[14px] text-body data-[focus]:bg-duplicate data-[selected]:font-medium data-[selected]:text-ink"
            >
              <span className="block truncate">{option.label}</span>
              {option.hint ? (
                <span className="mt-0.5 block truncate font-label text-[12px] text-faint">
                  {option.hint}
                </span>
              ) : null}
            </ListboxOption>
          ))}
        </ListboxOptions>
      </div>
    </Listbox>
  )
}

```

## FieldLabel

- File: `frontend/src/components/FieldRow.tsx`
- Description: Shared uppercase field caption with an optional adjacent information popover.
- Key props: `htmlFor`, `children`, `info?`, `infoLabel?`

```tsx
import InfoHint from './InfoHint'
import type { ReactNode } from 'react'

/**
 * The pad's field label.
 *
 * This file used to hold `FieldRow` as well — a 56px numbered rail down the
 * left of every field, from when the pad was one long numbered column. The pad
 * is stepped now and the numbers went with it: a step rail that already says
 * where you are does not need each field counting itself as well.
 */

type FieldLabelProps = {
  htmlFor: string
  children: ReactNode
  /**
   * The guidance that used to sit under the label as its own paragraph.
   *
   * Passed here rather than rendered by each caller so every field in the app
   * puts its ⓘ in the same place — immediately after the label text, on the
   * label's own line. A hint that moves around is a hint people stop looking
   * for.
   *
   * The icon is OUTSIDE the `<label>` element on purpose. A `<button>` inside
   * a `<label>` is one of the few nestings browsers actively punish: clicking
   * it would also activate the control the label points at, so opening the
   * hint would focus the textarea underneath — and on a checkbox it would
   * toggle the box.
   */
  info?: ReactNode
  /**
   * What to call this field in the icon's accessible name, when `children`
   * is not a plain string.
   *
   * Several labels interpolate — `Target cost ({currency}, {taxNote})` is an
   * array, not a string — and the `typeof children === 'string'` check below
   * would quietly fall back to "About this field" for every one of them.
   * That is precisely the announcement `InfoHint`'s own docstring rules out,
   * and it fails silently: the screen looks right and only a screen reader
   * hears the difference.
   */
  infoLabel?: string
}

/** Small caption above a control. */
export function FieldLabel({ htmlFor, children, info, infoLabel }: FieldLabelProps) {
  const label = (
    <label
      htmlFor={htmlFor}
      className="block font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void"
    >
      {children}
    </label>
  )

  // `mb-2` stays on the wrapper rather than the label, so a field with a hint
  // and a field without one leave exactly the same gap above their control.
  if (!info) return <div className="mb-2">{label}</div>

  return (
    <div className="mb-2 flex items-center gap-1.5">
      {label}
      <InfoHint label={infoLabel || (typeof children === 'string' ? children : 'this field')}>
        {info}
      </InfoHint>
    </div>
  )
}

```

## InfoHint

- File: `frontend/src/components/InfoHint.tsx`
- Description: Accessible information icon and anchored explanatory popover used by FieldLabel.
- Key props: `label`, `children`

```tsx
import { Popover, PopoverButton, PopoverPanel } from '@headlessui/react'
import type { ReactNode } from 'react'

/**
 * The small ⓘ beside a label, and the sentence it hides.
 *
 * The pad used to print its guidance as a paragraph under every label. That
 * reads well the first time and is furniture by the tenth: a studio that has
 * written forty quotations does not need to be told what a scope is, and the
 * text pushed the control it was explaining further down every screen. The
 * words are the same; they are behind a control now instead of in front of
 * the work.
 *
 * WHAT THIS COSTS, because it is a real trade and not a free tidy-up:
 * guidance you cannot see is guidance a first-time user does not know exists.
 * That is the argument for the icon being visible rather than the text
 * appearing on hover over the label - there is always something on screen
 * saying "there is more to know here", and it is in the same place every
 * time.
 *
 * A POPOVER, NOT A `title` ATTRIBUTE. A native tooltip cannot be opened by
 * touch at all, takes about a second to appear, cannot be styled, and is
 * skipped by several screen readers. This is a real button: it opens on
 * click, so it works on a phone; Escape and an outside click close it; and
 * Headless UI wires `aria-expanded` and `aria-controls` between the two
 * halves, so the panel is announced as the button's content rather than as
 * loose text somewhere in the page.
 */

type InfoHintProps = {
  /** What the icon is about, for the button's accessible name. A screen
   *  reader announcing forty buttons all called "More information" has told
   *  somebody nothing, so this is required rather than optional. */
  label: string
  children: ReactNode
}

export default function InfoHint({ label, children }: InfoHintProps) {
  return (
    <Popover className="relative inline-flex">
      {/* `align-middle` and the explicit box: this sits inline after a label
          whose text is 12px uppercase, and without a fixed size it would
          shift that label's baseline. */}
      <PopoverButton
        aria-label={`About ${label}`}
        className="inline-flex h-[18px] w-[18px] flex-none items-center justify-center rounded-full align-middle text-faint transition-colors duration-150 hover:text-ink focus:outline-none focus-visible:shadow-[var(--shadow-ring)] data-[open]:text-ink"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className="h-[15px] w-[15px]"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <circle cx="10" cy="10" r="7.25" />
          <path d="M10 9v4.4" strokeLinecap="round" />
          <path d="M10 6.4h.01" strokeLinecap="round" strokeWidth="1.9" />
        </svg>
      </PopoverButton>

      {/* `anchor` puts this in a portal at the end of <body>, which is the
          only way it escapes the `overflow-hidden` shells the whole app is
          built from - a panel positioned inside one would be clipped by it.
          `bottom start` keeps its left edge on the icon; the viewport padding
          lets it flip up when the field is near the bottom of the pad. */}
      <PopoverPanel
        anchor={{ to: 'bottom start', gap: 8, padding: 12 }}
        className="z-50 w-[min(20rem,calc(100vw-1.5rem))] rounded-xl border border-rule bg-paper p-3.5 shadow-raised"
      >
        {/* Deliberately NOT `text-void`. This is the one place the sentence
            appears now, so it is read rather than skimmed past, and it takes
            the body colour like anything else somebody is meant to read. */}
        <p className="font-body text-[13px] leading-[1.6] text-body">{children}</p>
      </PopoverPanel>
    </Popover>
  )
}

```

## ErrorNotice

- File: `frontend/src/components/ErrorNotice.tsx`
- Description: Dismissible inline error banner with a headline, recovery hint, and optional status code.
- Key props: `headline`, `next?`, `code?`, `onDismiss?`

```tsx
/**
 * What happened, and the next move. No apology, no stack trace, no shrug.
 *
 * A banner rather than a panel: an error is news, and news that cannot be put
 * down keeps the thing it is about off the screen. It states the problem in one
 * line, the remedy in the next, and closes - the failure is already in the
 * jobs page and in the notifications, so nothing is lost by dismissing it here.
 */

/**
 * The three parts of App's `describeError`, plus the way to close the banner.
 * `code` is the HTTP status when there was one and `null` when the failure
 * never reached the API.
 */
export type ErrorNoticeProps = {
  headline: string
  next?: string
  code?: number | null
  onDismiss?: () => void
}

export default function ErrorNotice({ headline, next, code, onDismiss }: ErrorNoticeProps) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-alert/40 bg-paper px-4 py-3"
    >
      <span
        aria-hidden="true"
        className="mt-[7px] h-1.5 w-1.5 flex-none rounded-pill bg-alert"
      />

      <div className="min-w-0 flex-1">
        <p className="font-body text-[14px] leading-snug text-ink">
          {headline}
          {code ? <span className="text-faint"> · {code}</span> : null}
        </p>
        {next ? (
          <p className="mt-0.5 font-body text-[13px] leading-[1.5] text-void">{next}</p>
        ) : null}
      </div>

      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss this message"
          className="-mr-1 -mt-1 flex h-7 w-7 flex-none items-center justify-center rounded-md text-faint transition-colors duration-150 hover:bg-duplicate hover:text-ink"
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 14 14"
            className="h-3 w-3"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          >
            <path d="M2 2l10 10M12 2L2 12" />
          </svg>
        </button>
      ) : null}
    </div>
  )
}

```

## ProgressBar and ProgressRing

- File: `frontend/src/components/ProgressBar.tsx`
- Description: Accessible linear and circular progress indicators with known and indeterminate states.
- Key props: Bar: `value?`, `label?`, `tone?`, `live?`; ring: `value?`, `label?`, `size?`

```tsx
/**
 * The Clarity Kit progress bar: an 8px track, a pine fill, a 450ms ease.
 *
 * The value is always something the server has actually reported. A job
 * declares its steps up front and marks them as each one genuinely comes back,
 * so a three-tier quotation moves in thirds. Nothing here animates on a timer —
 * a bar that fills smoothly while nothing has happened is a lie told in CSS,
 * and once one of them stalls at 90% nobody believes any of them again.
 *
 * A queued job has no percentage to give. It reports no value at all rather
 * than reporting zero, so a screen reader says "busy" instead of "0%".
 */

type ProgressBarProps = {
  value?: number
  label?: string
  tone?: 'accent' | 'alert'
  live?: boolean
}

export default function ProgressBar({
  value,
  label,
  tone = 'accent',
  live = false,
}: ProgressBarProps) {
  const known = typeof value === 'number' && Number.isFinite(value)
  const percent = known ? Math.round(Math.max(0, Math.min(1, value)) * 100) : 0

  return (
    <div
      className="h-2 overflow-hidden rounded-full bg-hairline"
      role="progressbar"
      aria-label={label || 'Progress'}
      {...(known ? { 'aria-valuenow': percent, 'aria-valuemin': 0, 'aria-valuemax': 100 } : {})}
    >
      {/* `live` puts a sheen on the fill, not on the width. The bar still only
          moves when a step genuinely finishes; the sheen is what distinguishes
          a job working through a forty-second tier from one that has stalled at
          the same number. */}
      <div
        className={`h-2 rounded-full transition-[width] duration-[450ms] ease-press ${
          tone === 'alert' ? 'bg-alert' : 'bg-ballpoint'
        } ${live ? 'bar-live' : ''}`}
        style={{ width: `${percent}%` }}
      />
    </div>
  )
}

type ProgressRingProps = {
  value?: number
  label?: string
  size?: number
}

/**
 * The kit's second shape for the same value — "use the ring when space is
 * tight". Used beside a running job on the pad, where a full-width bar under
 * the submit row would push the form around every time the stage changed.
 */
export function ProgressRing({ value, label, size = 44 }: ProgressRingProps) {
  const known = typeof value === 'number' && Number.isFinite(value)
  const fraction = known ? Math.max(0, Math.min(1, value)) : 0
  const radius = size / 2 - 4
  const circumference = 2 * Math.PI * radius

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className="-rotate-90 flex-none"
      role="img"
      aria-label={label || `${Math.round(fraction * 100)}% done`}
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--color-hairline)"
        strokeWidth="5"
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--color-ballpoint)"
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray={`${(circumference * fraction).toFixed(1)} 999`}
        className="transition-[stroke-dasharray] duration-[450ms] ease-press"
      />
    </svg>
  )
}

```

## TabStrip

- File: `frontend/src/components/TabStrip.tsx`
- Description: Keyboard-accessible segmented tab selector; panel rendering remains with the caller.
- Key props: `tabs`, `current`, `onSelect`, `label?`

```tsx
import { useRef } from 'react'
import type { KeyboardEvent } from 'react'

/**
 * The house switch, wearing proper tab semantics.
 *
 * Visually it is the segmented control the payment terms already use — a pill
 * on the secondary fill, the selected one lifted onto paper. What it adds is
 * the part a screen reader and a keyboard need: a real tablist, arrow keys
 * between the tabs, and one stop in the tab order rather than one per tab, so
 * Tab from the last tab lands in the panel instead of walking the strip.
 *
 * Panels are the caller's business. This only says which one is showing.
 */

type Tab = {
  id: string
  label: string
}

type TabStripProps = {
  tabs: Tab[]
  current: string
  onSelect: (id: string) => void
  label?: string
}

export default function TabStrip({ tabs, current, onSelect, label = 'Sections' }: TabStripProps) {
  const strip = useRef<HTMLDivElement | null>(null)

  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    // `| undefined` because any other key really is absent from this table,
    // which is what the guard on the next line already says.
    const keys: Record<string, number | undefined> = {
      ArrowRight: 1,
      ArrowDown: 1,
      ArrowLeft: -1,
      ArrowUp: -1,
    }
    const step = keys[event.key]
    if (!step) return
    event.preventDefault()

    const index = tabs.findIndex((tab) => tab.id === current)
    const next = tabs[(index + step + tabs.length) % tabs.length]
    onSelect(next.id)
    // Focus follows selection, which is what makes arrow keys feel like a
    // switch rather than a cursor.
    strip.current?.querySelector<HTMLElement>(`[data-tab="${next.id}"]`)?.focus()
  }

  return (
    <div
      ref={strip}
      role="tablist"
      aria-label={label}
      onKeyDown={move}
      className="flex gap-1 rounded-lg bg-duplicate p-1"
    >
      {tabs.map((tab) => {
        const selected = tab.id === current
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            data-tab={tab.id}
            id={`tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onSelect(tab.id)}
            className={`rounded-xs px-3 py-1.5 font-label text-[12px] uppercase tracking-[0.14em] ${
              selected ? 'bg-paper text-ink shadow-sheet' : 'text-void'
            }`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

```

## Toaster

- File: `frontend/src/components/Toaster.tsx`
- Description: Fixed, accessible alert/status toast rail used for asynchronous feedback.
- Key props: `toasts`, `onDismiss`

```tsx
import type { Toast } from '../lib/useToasts'

/**
 * The toast stack: bottom of the screen, above everything, touching nothing.
 *
 * Fixed rather than absolute, so it does not care what the screen underneath is
 * doing — a card that scrolls, a dialog that opens, a form that grows. The
 * layout below never shifts because a message arrived.
 *
 * An alert is `role="alert"`, which a screen reader announces immediately; a
 * note is `role="status"`, which waits for a pause. That is the actual
 * difference between "your password was wrong" and "check your inbox", and it
 * is the only thing the tone changes besides colour and how long it stays.
 *
 * The animation is `.toast` in index.css, inside the reduced-motion guard with
 * the other four — no component in this project animates anything itself.
 */

type ToasterProps = {
  toasts: Toast[]
  onDismiss: (id: number) => void
}

export default function Toaster({ toasts, onDismiss }: ToasterProps) {
  if (!toasts.length) return null

  return (
    <div
      // `pointer-events-none` on the rail, restored on each toast: the empty
      // space beside a toast must not swallow a click meant for the page.
      className="pointer-events-none fixed inset-x-0 bottom-0 z-[80] flex flex-col items-center gap-2 px-4 pb-4 sm:items-end sm:px-6 sm:pb-6"
    >
      {toasts.map((toast) => {
        const alert = toast.tone === 'alert'
        return (
          <div
            key={toast.id}
            role={alert ? 'alert' : 'status'}
            className={`toast pointer-events-auto flex w-full max-w-[26rem] items-start gap-3 rounded-xl border px-4 py-3 shadow-sheet ${
              alert ? 'border-alert/30 bg-alert-soft' : 'border-rule bg-paper'
            }`}
          >
            <span
              aria-hidden="true"
              className={`mt-[7px] h-1.5 w-1.5 flex-none rounded-full ${
                alert ? 'bg-alert' : 'bg-ballpoint'
              }`}
            />

            <div className="min-w-0 flex-1">
              <p className="font-body text-[14px] leading-[1.5] text-ink">{toast.message}</p>
              {toast.hint ? (
                <p className="mt-0.5 font-body text-[13px] leading-[1.5] text-void">{toast.hint}</p>
              ) : null}
            </div>

            <button
              type="button"
              onClick={() => onDismiss(toast.id)}
              aria-label="Dismiss"
              className="-mr-1 -mt-1 flex h-7 w-7 flex-none items-center justify-center rounded-md text-void transition-colors duration-150 hover:bg-hairline/60 hover:text-ink"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 14 14"
                className="h-3 w-3"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              >
                <path d="M2 2l10 10M12 2L2 12" />
              </svg>
            </button>
          </div>
        )
      })}
    </div>
  )
}

```


