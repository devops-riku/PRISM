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
        <span className="max-w-[12ch] truncate font-label text-[12px] uppercase tracking-[0.12em] text-void lg:max-w-[18ch]">
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
