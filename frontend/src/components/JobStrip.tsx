import { useEffect, useState } from 'react'
import { elapsedLabel } from '../lib/format'
import ProgressBar from './ProgressBar'
import type { Job } from '../types'

/**
 * What the server is doing right now, shown next to the button that asked for
 * it.
 *
 * Three things, in the order somebody needs them: what it is doing, how long it
 * has been doing it, and the fact that none of it depends on this tab staying
 * open. The last one is the whole point of background jobs — before them a
 * reload cost the Gemini spend — so the strip says it plainly rather than
 * leaving the reader to discover it by risking a quotation.
 *
 * **Where the percentage went.** PRISM counts progress from steps that have
 * actually finished, which is the right rule and has one honest consequence: a
 * job whose current step is a ninety-second model call sits at one number for
 * ninety seconds. Reported as "8% done" that reads as broken. So while nothing
 * has finished, the strip shows the kit's orbit — three dots turning, and a
 * sweeping thread — with the elapsed time beside it, which is the one true
 * number available. The percentage returns the moment a step completes and
 * there is something real to report.
 */

type JobStripProps = {
  job: Job | null
  pending: boolean
  verb?: string
}

export default function JobStrip({ job, pending, verb = 'quotation' }: JobStripProps) {
  const [seconds, setSeconds] = useState(0)

  // Ticks only while something is running, and resets when a new run starts.
  useEffect(() => {
    if (!pending) {
      setSeconds(0)
      return undefined
    }
    const started = Date.now()
    const timer = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [pending, job?.id])

  // `pending` alone, deliberately. The watcher keeps the last job it followed —
  // the app never unmounts — so a strip that also rendered on `job` would sit
  // over an empty form afterwards, full and green, still promising that work
  // continues in the background hours after it finished.
  if (!pending) return null

  const queued = !job || job.state === 'queued'
  const done = job ? job.steps.filter((step) => step.done).length : 0
  const total = job?.steps?.length || 0
  const percent = job ? Math.round(job.progress * 100) : 0
  const stage = job?.stage || 'Sending the brief'

  // Nothing has finished yet, so there is nothing to put a number on.
  const waiting = queued || done === 0

  return (
    <div className="rounded-lg border border-hairline bg-duplicate/50 px-4 py-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="flex items-center gap-2 font-body text-[14px] text-body">
          {stage}
          {job?.state === 'running' ? (
            <span className="orbit" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          ) : null}
        </p>
        <p className="font-label text-[14px] tabular-nums text-faint" aria-hidden="true">
          {waiting
            ? elapsedLabel(seconds)
            : `${percent}% · ${done} of ${total} · ${elapsedLabel(seconds)}`}
        </p>
      </div>

      <div className="mt-2.5">
        {waiting ? (
          <div
            className="thread thread--waiting rounded-pill"
            role="progressbar"
            aria-label={`Preparing the ${verb}: ${stage}`}
          />
        ) : (
          <ProgressBar
            value={job.progress}
            live={job.state === 'running'}
            label={`Preparing the ${verb}: ${stage}`}
          />
        )}
      </div>

      <p className="mt-2.5 font-label text-[12px] text-void">
        This keeps going if you close the tab.{' '}
        <a href="#/jobs" className="text-ballpoint underline underline-offset-2">
          In progress
        </a>{' '}
        has every run, finished and unfinished.
      </p>
    </div>
  )
}
