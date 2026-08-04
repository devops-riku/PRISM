import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { listJobs } from '../lib/api'
import { elapsedLabel, formatDate, secondsSince } from '../lib/format'
import { onJobUpdate } from '../lib/notifications'
import ProgressBar from './ProgressBar'
import { ACTION, CARD, DISPLAY, MONO_LABEL } from './tokens'
import type { Job } from '../types'

/**
 * Work in progress, and everything already done.
 *
 * Preparing a quotation takes the better part of a minute; three tiers take a
 * minute and a half. That used to be time somebody had to sit through, because
 * navigating away lost the work and the Gemini spend with it. Now the request
 * starts a job and returns, and this is where the job is — including after a
 * reload, a different browser, or a restart of the API.
 *
 * The bar reports steps the server has actually finished. A three-tier job
 * moves in thirds as each tier genuinely comes back. Nothing here interpolates
 * against a clock — and while no step has finished there is no percentage to
 * give, so the row shows the orbit and the elapsed time instead of the "8%
 * done" that sat there unchanged for a minute and a half and read as broken.
 *
 * Updates arrive two ways: pushed on the notifications socket the moment the
 * server touches a job, and polled every 1.5s as the floor. The poll is what
 * makes the page correct; the push is what makes it quick.
 *
 * Two lists rather than one, because they are read differently: the top is
 * something to wait on, the bottom is something to open.
 */

const POLL_MS = 1500

const RUNNING = new Set(['queued', 'running'])

/**
 * Where a finished job's result lives.
 *
 * Not every job makes a quotation any more. A proposal job returns a document
 * id, and sending that to the quotation page produced a confident 404 about a
 * quotation nobody had asked for - the id was real, the page was wrong.
 */
function resultHref(job: Job): string {
  return job.kind === 'proposal' ? '#/p/' : '#/q/'
}

export default function JobList() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const live = useRef(true)
  const timer = useRef<number>(0)

  const load = useCallback(
    () =>
      listJobs()
        .then((found) => {
          if (!live.current) return found
          setJobs(found)
          setError('')
          return found
        })
        .catch((failure: unknown) => {
          // `failure?.message` on an `unknown`: the same read, narrowed rather
          // than assumed. Only a string message is a message; anything else
          // falls through to the sentence below, as an absent one already did.
          const message =
            typeof failure === 'object' &&
            failure !== null &&
            'message' in failure &&
            typeof failure.message === 'string'
              ? failure.message
              : ''
          if (live.current) setError(message || 'The job list did not load.')
          return []
        })
        .finally(() => {
          if (live.current) setLoading(false)
        }),
    [],
  )

  const running = jobs.filter((job) => RUNNING.has(job.state))
  const settled = jobs.filter((job) => !RUNNING.has(job.state))
  const anyRunning = running.length > 0

  useEffect(() => {
    live.current = true
    load()
    return () => {
      live.current = false
    }
  }, [load])

  // The floor under the push: ask again while something is moving, and stop
  // when nothing is. Keyed on `anyRunning` rather than chained from the last
  // response, so a job that arrives on the socket — started in another tab —
  // starts the poll too, and the page is not left depending on a socket that
  // may have dropped.
  useEffect(() => {
    if (!anyRunning) return undefined
    let stopped = false

    const tick = () => {
      timer.current = window.setTimeout(
        () =>
          load().finally(() => {
            if (!stopped) tick()
          }),
        POLL_MS,
      )
    }

    tick()
    return () => {
      stopped = true
      window.clearTimeout(timer.current)
    }
  }, [anyRunning, load])

  // Pushed updates, on the socket the notification bell already keeps open. A
  // job touched on the server lands here in the same second rather than at the
  // next tick, and a run started in another tab appears without one at all.
  useEffect(
    () =>
      onJobUpdate((pushed) => {
        if (!live.current || !pushed?.id) return
        setJobs((current) => {
          const at = current.findIndex((job) => job.id === pushed.id)
          if (at < 0) return [pushed, ...current]
          const next = current.slice()
          next[at] = pushed
          return next
        })
      }),
    [],
  )

  // One clock for the page, ticking only while something is running. Elapsed is
  // measured from the server's own `created_at`, so a page opened forty seconds
  // into a job says forty seconds rather than starting again from zero.
  useEffect(() => {
    if (!anyRunning) return undefined
    const tick = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(tick)
  }, [anyRunning])

  return (
    <div className="no-scrollbar flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto">
      <section aria-labelledby="jobs-running" className={`${CARD} shrink-0`}>
        <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-rule px-5 py-3 sm:px-6">
          <h2 id="jobs-running" className={`${DISPLAY} text-[18px]`}>
            Being prepared
          </h2>
          <p className={MONO_LABEL}>
            {loading && !jobs.length
              ? 'Reading'
              : running.length
                ? `${running.length} running`
                : 'Nothing running'}
          </p>
        </div>

        {error ? (
          <p role="alert" className="border-b border-rule px-5 py-3 font-body text-[15px] sm:px-6">
            <span className="block font-label text-[12px] uppercase tracking-[0.14em] text-alert">
              Not loaded
            </span>
            <span className="mt-1 block">{error}</span>
          </p>
        ) : null}

        {!running.length && !error && !(loading && !jobs.length) ? (
          <p className="px-5 py-8 text-center font-body text-[15px] text-void sm:px-6">
            Nothing is being prepared. Start one on the pad — it keeps going if you close this tab,
            and finishes here.
          </p>
        ) : null}

        {running.map((job, index) => {
          const done = job.steps.filter((step) => step.done).length
          // Nothing has finished, so there is no percentage to report — only
          // how long it has been going.
          const waiting = job.state === 'queued' || done === 0
          const elapsed = elapsedLabel(secondsSince(job.created_at, now))

          return (
            // py-3, matching the Finished rows below and every other
            // row-touch list in the app (IntakeListScreen, ProposalList,
            // WorkspacesScreen) — this section used to sit at py-4, a step
            // taller than its own sibling two sections down.
            <article
              key={job.id}
              style={{ '--i': index } as CSSProperties}
              className="row-touch rise-in border-b border-hairline px-5 py-3 last:border-b-0 sm:px-6"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                <h3 className="font-body text-[15px] font-medium text-ink">{job.title}</h3>
                <p className="font-label text-[14px] tabular-nums text-faint">
                  {job.state === 'queued'
                    ? 'Queued'
                    : waiting
                      ? elapsed
                      : `${Math.round(job.progress * 100)}% · ${done} of ${job.steps.length} · ${elapsed}`}
                </p>
              </div>

              <div className="mt-2.5">
                {waiting ? (
                  <div
                    className="thread thread--waiting rounded-pill"
                    role="progressbar"
                    aria-label={`${job.title}: ${job.stage}`}
                  />
                ) : (
                  <ProgressBar
                    value={job.progress}
                    live={job.state === 'running'}
                    label={`${job.title}: ${job.stage}`}
                  />
                )}
              </div>

              <p className="mt-2.5 flex items-center gap-2 font-body text-[13px] text-void">
                {job.stage}
                {job.detail ? <span className="text-faint">· {job.detail}</span> : null}
                {job.state === 'running' ? (
                  <span className="orbit" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                ) : null}
              </p>
            </article>
          )
        })}
      </section>

      <section aria-labelledby="jobs-finished" className={`${CARD} shrink-0`}>
        <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-rule px-5 py-3 sm:px-6">
          <h2 id="jobs-finished" className={`${DISPLAY} text-[18px]`}>
            Finished
          </h2>
          <p className={MONO_LABEL}>{settled.length} on file</p>
        </div>

        {!settled.length ? (
          <p className="px-5 py-8 text-center font-body text-[15px] text-void sm:px-6">
            Nothing has finished yet.
          </p>
        ) : null}

        {/* Keyed by job id in a parent of its own, so the poll and the socket
            re-render these rows without remounting them and the arrival runs
            once. The one row that DOES remount is a job crossing from Being
            prepared into Finished - a different parent, so React unmounts it
            there and mounts it here. That is the right moment to animate: it
            genuinely just arrived. */}
        {settled.map((job, index) => (
          <article
            key={job.id}
            style={{ '--i': index } as CSSProperties}
            className="row-touch rise-in border-b border-hairline px-5 py-3 last:border-b-0 sm:px-6"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-3">
              <div className="min-w-[16rem] flex-1">
                <h3 className="font-body text-[15px] text-ink">{job.title}</h3>
                <p className="mt-2 flex flex-wrap items-center gap-2">
                  <span className={job.state === 'failed' ? 'chip chip--alert' : 'chip'}>
                    {job.state === 'failed' ? 'Stopped' : 'Ready'}
                  </span>
                  <span className={MONO_LABEL}>
                    {formatDate(job.finished_at || job.created_at, { withTime: true })}
                    {job.result_ids.length > 1 ? ` · ${job.result_ids.length} quotations` : ''}
                  </span>
                </p>
              </div>

              {job.result_ids.length ? (
                <span className="flex flex-wrap gap-2">
                  {job.result_ids.map((id, index) => (
                    <a key={id} href={`${resultHref(job)}${id}`} className={ACTION}>
                      {job.result_ids.length > 1 ? `Open ${index + 1}` : 'Open'}
                    </a>
                  ))}
                </span>
              ) : null}
            </div>

            {job.state === 'failed' && job.error ? (
              <p className="mt-3 border-l-2 border-l-alert pl-4 font-body text-[15px] text-body">
                {job.error}
              </p>
            ) : null}
          </article>
        ))}
      </section>
    </div>
  )
}
