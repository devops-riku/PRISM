import { useEffect, useState } from 'react'
import {
  createWorkspace,
  currentWorkspace,
  listWorkspaces,
  renameWorkspace,
  setCurrentWorkspace,
} from '../lib/api'
import RowMenu from './RowMenu'
import { useRole } from '../lib/role'
import { ACTION, ACTION_PRIMARY, DISPLAY, MONO_LABEL, WELL } from './tokens'
import type { Workspace } from '../types'

/**
 * The books of work on this install, and the way to start another.
 *
 * With none, this is the whole app: nothing can be quoted before there is
 * somewhere to file it, so the screen asks for one name and nothing else. That
 * is deliberate — a workspace invented automatically would be a book nobody
 * named and nobody recognises, and the first thing anybody did would be to
 * rename it.
 */

type EmptyProps = {
  onCreate: (name: string) => void
  busy: boolean
  error: string
}

function Empty({ onCreate, busy, error }: EmptyProps) {
  const [name, setName] = useState('')

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center">
      <div className="w-full max-w-[26rem] text-center">
        <h1 className={`${DISPLAY} text-[22px] text-ink`}>Name your first workspace</h1>
        <p className="mx-auto mt-3 max-w-[34ch] font-body text-[15px] leading-[1.6] text-void">
          A workspace holds one studio&rsquo;s settings, rates, terms and every quotation it
          produces.
        </p>

        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (name.trim()) onCreate(name.trim())
          }}
          className="mt-6"
        >
          <label htmlFor="first_workspace" className="sr-only">
            Workspace name
          </label>
          <input
            id="first_workspace"
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Neptune Labs"
            className={`${WELL} text-center`}
          />
          <button
            type="submit"
            disabled={!name.trim() || busy}
            className={`${ACTION_PRIMARY} mt-4 w-full`}
          >
            {busy ? 'Creating' : 'Create workspace'}
          </button>
        </form>

        {error ? (
          <p role="alert" className="mt-4 font-body text-[14px] text-alert">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  )
}

type WorkspacesScreenProps = {
  onChanged?: (rows: Workspace[]) => void
}

export default function WorkspacesScreen({ onChanged }: WorkspacesScreenProps) {
  const { isAdmin } = useRole()
  const [rows, setRows] = useState<Workspace[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [renamingId, setRenamingId] = useState('')
  const [renameDraft, setRenameDraft] = useState('')

  const chosen = currentWorkspace()

  const load = () =>
    listWorkspaces()
      .then((found) => {
        setRows(found)
        setError('')
        if (onChanged) onChanged(found)
      })
      .catch((failure) => {
        setRows([])
        setError(failure?.message || 'The workspaces did not load.')
      })

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const create = (name: string) => {
    setBusy(true)
    setError('')
    createWorkspace(name)
      .then((made) => {
        setCurrentWorkspace(made.id)
        window.location.reload()
      })
      .catch((failure) => setError(failure?.message || 'That workspace was not created.'))
      .finally(() => setBusy(false))
  }

  const open = (id: string) => {
    setCurrentWorkspace(id)
    window.location.hash = '#/'
    window.location.reload()
  }

  const rename = (id: string) => {
    const name = renameDraft.trim()
    if (!name) return
    renameWorkspace(id, name)
      .then(() => {
        setRenamingId('')
        setRenameDraft('')
        load()
      })
      .catch((failure) => setError(failure?.message || 'That workspace was not renamed.'))
  }

  if (rows === null) {
    return (
      <p className="px-5 py-12 text-center font-label text-[12px] uppercase tracking-[0.14em] text-void">
        Reading
      </p>
    )
  }

  if (rows.length === 0) return <Empty onCreate={create} busy={busy} error={error} />

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <section className="flex min-h-0 flex-1 flex-col rounded-xl border border-rule bg-paper shadow-sheet">
        {/* The title and nothing else. A count of workspaces beside a list of
            them is the list counted twice.

            Who is on a workspace used to hang underneath this list. It has its
            own page now: this answers "which book of work am I in", and that
            answers "who else can see it" — two questions, and stacking them
            meant every trip to switch workspace ended in a roster nobody had
            asked for. */}
        <div className="flex shrink-0 items-baseline gap-3 border-b border-rule px-5 py-3 sm:px-6">
          <h2 className={`${DISPLAY} text-[20px]`}>Workspaces</h2>
        </div>

        {error ? (
          <p role="alert" className="border-b border-rule px-5 py-3 font-body text-[15px] sm:px-6">
            <span className="block font-label text-[12px] uppercase tracking-[0.14em] text-alert">
              Not done
            </span>
            <span className="mt-1 block">{error}</span>
          </p>
        ) : null}

        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto pb-2">
          {rows.map((row) => {
            const here = row.id === chosen || (!chosen && row === rows[0])
            return (
              <article
                key={row.id}
                className="row-touch flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-hairline px-5 py-3 last:border-b-0 sm:px-6"
              >
                <div className="min-w-[14rem] flex-1">
                  {renamingId === row.id ? (
                    <span className="flex flex-wrap items-center gap-2">
                      <input
                        autoFocus
                        value={renameDraft}
                        onChange={(event) => setRenameDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') rename(row.id)
                          if (event.key === 'Escape') setRenamingId('')
                        }}
                        className={`${WELL} max-w-[240px] py-1.5`}
                      />
                      <button type="button" className={ACTION} onClick={() => rename(row.id)}>
                        Save
                      </button>
                      <button type="button" className={ACTION} onClick={() => setRenamingId('')}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    // The whole block, not the name alone. A row whose only
                    // target was six words of text meant aiming at them, while
                    // the obvious place to click - anywhere on the row - did
                    // nothing.
                    //
                    // A <button> filling the block rather than a click handler
                    // on the <article>: the menu beside it is interactive too,
                    // and a button inside a button is invalid HTML that
                    // browsers resolve by dropping one of them. Two siblings in
                    // a flex row give the large target without nesting, and the
                    // block stays reachable by Tab as one stop.
                    <button
                      type="button"
                      onClick={() => open(row.id)}
                      className="block w-full text-left"
                    >
                      <span className="block font-body text-[15px] text-ink">{row.name}</span>
                      <span className={`${MONO_LABEL} mt-1 block`}>
                        {[
                          row.studio_name,
                          `${row.quotations} quotations`,
                          `${row.proposals} proposals`,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </span>
                    </button>
                  )}
                </div>

                {/* Not an action, and never was: this renders only on the
                    workspace you are already in. It said "Open", which beside a
                    row reads as a verb - the whole reason the row looked like
                    it needed a button. "Current" says the same fact and cannot
                    be mistaken for an instruction, and the dot matches the one
                    the header already puts before this workspace's name. */}
                {here ? (
                  <span className="flex items-center gap-1.5 font-label text-[12px] uppercase tracking-[0.14em] text-ballpoint">
                    <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-ballpoint" />
                    Current
                  </span>
                ) : null}

                {/* No delete here. It lives in that workspace's own Settings,
                    under Danger zone, where the rates and terms it would take
                    with it are on the same screen. */}
                <RowMenu
                  label={`Actions for workspace ${row.name}`}
                  items={[
                    { label: 'Open', onSelect: () => open(row.id) },
                    // Renaming somebody else's workspace is an admin's to do.
                    ...(isAdmin
                      ? [
                          {
                            label: 'Rename',
                            onSelect: () => {
                              setRenameDraft(row.name)
                              setRenamingId(row.id)
                            },
                          },
                        ]
                      : []),
                  ]}
                />
              </article>
            )
          })}
        </div>
      </section>
    </div>
  )
}
