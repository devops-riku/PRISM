import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchJob } from './api'
import { onJobUpdate } from './notifications'
import type { Job } from '../types'

/** What the caller wants told, and how often to ask. */
export type UseJobWatchOptions = {
  onDone?: (resultIds: string[], job: Job) => void
  onFail?: (job: Job) => void
  intervalMs?: number
}

/** The job as it currently stands, and the way to start following one. */
export type UseJobWatchResult = {
  job: Job | null
  watch: (started: Job) => void
}

/**
 * Follow one background job to its end.
 *
 * The pad and the revision slip both start work that outlives the request that
 * asked for it, and both need the same three things: the job as it currently
 * stands, a hand-off when it finishes, and a message when it does not.
 *
 * Polling rather than a socket. A quotation reports four or five real events
 * over ninety seconds — a stream is a lot of moving parts to deliver five
 * messages, and polling degrades into "the API is not answering" on its own,
 * which is a thing this client already knows how to say.
 *
 * The handlers are held in a ref so a caller can pass inline closures without
 * restarting the poll on every render.
 */
export function useJobWatch({
  onDone,
  onFail,
  intervalMs = 1500,
}: UseJobWatchOptions = {}): UseJobWatchResult {
  const [job, setJob] = useState<Job | null>(null)
  const timer = useRef<number>(0)
  const live = useRef(true)
  const handlers = useRef({ onDone, onFail })
  handlers.current = { onDone, onFail }
  // Which job this hook is following, so a push about somebody else's - or
  // about the run before this one - is ignored rather than rendered.
  const following = useRef('')
  const settled = useRef(false)

  useEffect(() => {
    live.current = true
    return () => {
      live.current = false
      window.clearTimeout(timer.current)
    }
  }, [])

  // Pushed updates, on the socket the notifications already keep open. The
  // poll below is untouched: this is a way of hearing sooner, not the only way
  // of hearing. A step that finishes on the server lands here in the same
  // second instead of up to 1.5s later, which is the difference between a bar
  // that moves when something happens and a bar that looks stuck.
  useEffect(
    () =>
      onJobUpdate((pushed) => {
        if (!live.current || !pushed?.id || pushed.id !== following.current) return
        setJob(pushed)

        if (pushed.state === 'done' || pushed.state === 'failed') {
          if (settled.current) return
          settled.current = true
          window.clearTimeout(timer.current)
          if (pushed.state === 'done') handlers.current.onDone?.(pushed.result_ids, pushed)
          else handlers.current.onFail?.(pushed)
        }
      }),
    [],
  )

  const poll = useCallback(
    (jobId: string) => {
      fetchJob(jobId)
        .then((latest) => {
          if (!live.current) return
          setJob(latest)

          // Whichever arrives first - this poll or a push - finishes the run
          // exactly once. Two `onDone`s would open the same quotation twice.
          if (latest.state === 'done' || latest.state === 'failed') {
            if (settled.current) return
            settled.current = true
            if (latest.state === 'done') handlers.current.onDone?.(latest.result_ids, latest)
            else handlers.current.onFail?.(latest)
            return
          }
          timer.current = window.setTimeout(() => poll(jobId), intervalMs)
        })
        .catch((failure: unknown) => {
          if (!live.current) return
          // The work is on the server and does not care that one poll missed.
          // A dropped Wi-Fi connection should not end up reported as a failed
          // quotation, so this keeps asking; the job page is the other way to
          // find the result if this tab never recovers.
          if (
            typeof failure === 'object' &&
            failure !== null &&
            'kind' in failure &&
            failure.kind === 'aborted'
          )
            return
          timer.current = window.setTimeout(() => poll(jobId), intervalMs * 2)
        })
    },
    [intervalMs],
  )

  /** Start following a job returned by createProposal or reviseProposal. */
  const watch = useCallback(
    (started: Job) => {
      if (!started?.id) return
      window.clearTimeout(timer.current)
      following.current = started.id
      settled.current = false
      setJob(started)
      timer.current = window.setTimeout(() => poll(started.id), intervalMs)
    },
    [poll, intervalMs],
  )

  // `job` outlives the run: it holds the last state the server reported, and
  // nothing resets it. Callers decide what to show from their own pending flag
  // rather than from the presence of a job — a strip that rendered on `job`
  // alone would sit over an empty form long after the work was done.
  return { job, watch }
}
