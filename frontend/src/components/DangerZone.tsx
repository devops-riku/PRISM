import { useEffect, useState } from 'react'
import { currentWorkspace, deleteWorkspace, listWorkspaces, setCurrentWorkspace } from '../lib/api'
import type { Workspace } from '../types'
import { ACTION, MONO_LABEL, WELL } from './tokens'

/**
 * Deleting this workspace, and everything filed in it.
 *
 * It lives in Settings because Settings is the one screen that is already about
 * this workspace and nothing else — every rate, clause and default on the other
 * tabs belongs to it. It is behind its own tab, and behind typing the name,
 * because the counts beside the button are the whole warning: a click here ends
 * every quotation and proposal the studio has produced.
 *
 * The last workspace can go too. An install with no workspace is a valid state —
 * it is what a new one is — and the app asks for a name when there is none.
 */

export default function DangerZone() {
  const [rows, setRows] = useState<Workspace[] | null>(null)
  const [typed, setTyped] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    listWorkspaces()
      .then((found) => live && setRows(found))
      // The rejection is annotated with what is actually read off it rather
      // than left as the `any` a promise hands a catch.
      .catch((failure: { message?: string }) => {
        if (live) {
          setRows([])
          setError(failure?.message || 'The workspaces did not load.')
        }
      })
    return () => {
      live = false
    }
  }, [])

  const chosen = currentWorkspace()
  const here = rows ? rows.find((row) => row.id === chosen) || rows[0] || null : null
  const matches = here ? typed.trim().toLowerCase() === here.name.trim().toLowerCase() : false

  const handleDelete = () => {
    if (!here || !matches || busy) return
    setBusy(true)
    setError('')
    deleteWorkspace(here.id)
      .then(() => {
        setCurrentWorkspace('')
        window.location.hash = '#/'
        window.location.reload()
      })
      .catch((failure: { message?: string }) => {
        setError(failure?.message || 'That workspace was not deleted.')
        setBusy(false)
      })
  }

  if (rows === null) {
    return <p className={MONO_LABEL}>Reading</p>
  }

  if (!here) {
    return (
      <p className="font-body text-[15px] text-void">
        There is no workspace open, so there is nothing here to delete.
      </p>
    )
  }

  return (
    <div className="max-w-[46rem]">
      <div className="rounded-lg border border-alert/40 bg-paper p-5">
        <h3 className="font-label text-[12px] font-medium uppercase tracking-[0.14em] text-alert">
          Delete this workspace
        </h3>
        <p className="mt-2 font-body text-[14px] leading-[1.6] text-void">
          Deletes <span className="text-ink">{here.name}</span> and everything filed in it —{' '}
          <span className="tabular-nums text-ink">{here.quotations}</span> quotations,{' '}
          <span className="tabular-nums text-ink">{here.proposals}</span> proposals, its rate card,
          its terms, its template and its numbering. Nothing is recoverable and no other workspace
          is touched.
        </p>

        <label htmlFor="confirm_workspace" className={`${MONO_LABEL} mt-5 block`}>
          Type “{here.name}” to confirm
        </label>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <input
            id="confirm_workspace"
            value={typed}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setTyped(event.target.value)}
            placeholder={here.name}
            className={`${WELL} max-w-[280px]`}
          />
          <button
            type="button"
            disabled={!matches || busy}
            onClick={handleDelete}
            className={`${ACTION} border-alert text-alert disabled:opacity-40`}
          >
            {busy ? 'Deleting' : 'Delete workspace'}
          </button>
        </div>

        {rows.length === 1 ? (
          <p className="mt-3 font-body text-[13px] leading-[1.6] text-void">
            This is the only workspace. Deleting it leaves the app empty, and it will ask you to
            name a new one.
          </p>
        ) : null}

        {error ? (
          <p role="alert" className="mt-3 font-body text-[14px] text-alert">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  )
}
