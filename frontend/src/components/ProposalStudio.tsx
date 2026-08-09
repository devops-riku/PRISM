import { useCallback, useEffect, useState } from 'react'
import { buildDocument, listProposals } from '../lib/api'
import { formatDate, formatMoney } from '../lib/format'
import { useJobWatch } from '../lib/useJobWatch'
import JobStrip from './JobStrip'
import { ACTION_PRIMARY, CARD, DISPLAY, MONO_LABEL, WELL } from './tokens'
import type { ProposalSummary } from '../types'

/**
 * Build a proposal from a quotation.
 *
 * A proposal is built from ONE quotation, exactly as that quotation stands.
 * Tiers are not merged and revisions are not followed: a studio selling the
 * Standard tier picks Standard. That is stated on this screen rather than
 * assumed, because it is the one thing somebody can get wrong here.
 *
 * The split of authorship is the point of the whole feature, so the screen says
 * it plainly: the model writes the argument, the figures come from the
 * quotation, and the terms are the studio's own words from Settings.
 *
 * What is built lives on its own page. This one is a form you use once per
 * proposal; the list is a thing you come back to, and stacking them made the
 * screen grow every time somebody used it.
 */

export default function ProposalStudio() {
  const [quotations, setQuotations] = useState<ProposalSummary[]>([])
  const [query, setQuery] = useState('')
  const [chosen, setChosen] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  const load = useCallback(
    () =>
      listProposals({ q: query })
        .then((found) => {
          setQuotations(found)
          setError('')
        })
        // `failure` takes its type from `Promise.catch`, which the standard
        // library declares as `any`. Narrowing it to `unknown` would force
        // `?.message` to be rewritten, and every rewrite of it changes what a
        // falsy message falls back to.
        .catch((failure) => setError(failure?.message || 'This screen did not load.'))
        .finally(() => setLoading(false)),
    [query],
  )

  useEffect(() => {
    load()
  }, [load])

  const build = useJobWatch({
    onDone: (resultIds) => {
      setPending(false)
      load()
      const id = resultIds[0]
      if (id) window.location.hash = `#/p/${id}`
    },
    onFail: (job) => {
      setPending(false)
      setError(job.error || 'The proposal was not built.')
    },
  })

  const { watch } = build

  const handleBuild = () => {
    if (pending || !chosen) return
    setPending(true)
    setError('')
    buildDocument(chosen)
      .then((job) => watch(job))
      .catch((failure) => {
        setPending(false)
        setError(failure?.message || 'The proposal was not started.')
      })
  }

  return (
    <div className="no-scrollbar flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto">
      <section aria-labelledby="build-proposal" className={`${CARD} shrink-0`}>
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-rule px-5 py-3 sm:px-6">
          <h2 id="build-proposal" className={`${DISPLAY} text-[18px]`}>
            Build a proposal
          </h2>
          <div className="flex w-full flex-wrap items-center gap-3 sm:w-auto sm:flex-nowrap sm:gap-4">
            {/* The way back to what has been built, from the screen that builds
                it. The list is its own page now, and this is the one place
                somebody would look for it without going home first. */}
            <a
              href="#/documents"
              className="font-label text-[12px] uppercase tracking-[0.14em] text-ballpoint no-underline hover:underline"
            >
              Proposals
            </a>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find a quotation"
              aria-label="Find a quotation to build from"
              className={`${WELL} w-full py-2 sm:w-[240px]`}
            />
          </div>
        </div>

        <p className="border-b border-hairline px-5 py-3 font-body text-[13px] leading-[1.6] text-void sm:px-6">
          Pick the quotation you are selling. A proposal is built from that one exactly as it stands
          — tiers are not merged and revisions are not followed, so choose the tier you intend to
          win. PRISM writes the argument, prints every figure from the quotation, and adds your
          terms from Settings word for word.
        </p>

        {error ? (
          <p role="alert" className="border-b border-rule px-5 py-3 font-body text-[15px] sm:px-6">
            <span className="block font-label text-[12px] uppercase tracking-[0.14em] text-alert">
              Not done
            </span>
            <span className="mt-1 block">{error}</span>
          </p>
        ) : null}

        <div className="no-scrollbar max-h-[38vh] overflow-y-auto">
          {!loading && quotations.length === 0 ? (
            <p className="px-5 py-8 text-center font-body text-[15px] text-void sm:px-6">
              {query
                ? `Nothing on file matches “${query}”.`
                : 'Nothing quoted yet. Prepare a quotation first — a proposal is built on one.'}
            </p>
          ) : null}

          {quotations.map((row) => {
            const active = chosen === row.id
            return (
              <label
                key={row.id}
                className={`row-touch flex cursor-pointer items-center gap-3 border-b border-hairline px-5 py-2.5 last:border-b-0 sm:px-6 ${
                  active ? 'bg-accent-soft' : ''
                }`}
              >
                <input
                  type="radio"
                  name="quotation"
                  value={row.id}
                  checked={active}
                  onChange={() => setChosen(row.id)}
                  disabled={pending}
                  className="h-4 w-4 flex-none accent-ballpoint"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-body text-[14px] text-ink">
                    {row.project_name || 'Untitled'}
                    {row.tier_name ? <span className="text-void"> · {row.tier_name}</span> : null}
                  </span>
                  <span className={`${MONO_LABEL} mt-0.5 block truncate`}>
                    {[row.client_name, row.quotation_ref || row.id, formatDate(row.created_at)]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </span>
                <span className="flex-none font-label text-[13px] tabular-nums text-ink">
                  {formatMoney(row.total, row.currency)}
                </span>
              </label>
            )
          })}
        </div>

        {pending ? (
          <div className="border-t border-rule px-5 py-4 sm:px-6">
            <JobStrip job={build.job} pending={pending} verb="proposal" />
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-4 border-t border-rule px-5 py-3 sm:px-6">
          <p className="font-body text-[13px] text-void" aria-live="polite">
            {pending
              ? ''
              : chosen
                ? 'Ready. The proposal is a new document; the quotation is untouched.'
                : 'Choose a quotation above.'}
          </p>
          <button
            type="button"
            onClick={handleBuild}
            disabled={!chosen || pending}
            className={`${ACTION_PRIMARY} ${pending ? 'cursor-wait' : ''}`}
          >
            {pending ? 'Building' : 'Build proposal'}
          </button>
        </div>
      </section>

    </div>
  )
}
