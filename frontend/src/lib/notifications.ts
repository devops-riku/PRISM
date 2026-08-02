import { useEffect, useState } from 'react'
import {
  listNotifications,
  markNotificationsRead,
  clearNotifications,
  currentWorkspace,
} from './api'
import { accessToken, onSession } from './auth'
import type { Job, Note } from '../types'

/**
 * A socket for speed, a poll for certainty.
 *
 * Notifications arrive over a WebSocket the moment the server writes them —
 * `/api/notifications/stream`, authenticated by sending the token in the first
 * frame rather than putting a live session in a URL. When that socket is open
 * the bell is immediate.
 *
 * The poll stays underneath it, slowed to two minutes. A push nobody can fall
 * back from is a push that eventually drops a message and never notices: a
 * laptop lid, a proxy that eats upgrades, a server restart. So the floor is a
 * timer that cannot fail interestingly, and the socket is the part that makes
 * it feel instant.
 *
 * One module-level store with a listener set — the shape `role.js` already
 * uses — so the bell and the panel read one source rather than two.
 *
 * A feed that cannot load says nothing. It never raises an error over an app
 * that is otherwise working; the next tick simply tries again, slower.
 */

/** The whole of what this module knows, handed to every listener at once. */
export type NotificationState = {
  unread: number
  notes: Note[]
  reading: boolean
}

/** Told about a job the moment the server pushes it. */
type JobWatcher = (job: Job) => void

/**
 * One frame off the socket — the five shapes `main.py` and `inbox.py` send.
 *
 * The keys a frame does not carry are declared `?: undefined` rather than left
 * out: the handler below asks `if (payload.job)`, which a plain union would
 * refuse on the members that have no `job`, and `'job' in payload` is a
 * different question from the one the code asks.
 */
type ReadyFrame = {
  ready: true
  unread: number
  beat?: undefined
  error?: undefined
  note?: undefined
  job?: undefined
}
type BeatFrame = {
  beat: true
  ready?: undefined
  unread?: undefined
  error?: undefined
  note?: undefined
  job?: undefined
}
type ErrorFrame = {
  error: string
  ready?: undefined
  unread?: undefined
  beat?: undefined
  note?: undefined
  job?: undefined
}
type NoteFrame = {
  note: Note
  unread: number
  ready?: undefined
  beat?: undefined
  error?: undefined
  job?: undefined
}
type JobFrame = {
  job: Job
  ready?: undefined
  unread?: undefined
  beat?: undefined
  error?: undefined
  note?: undefined
}
type StreamFrame = ReadyFrame | BeatFrame | ErrorFrame | NoteFrame | JobFrame

//: With a socket open this is only a safety net. Without one it is the feature,
//: so it tightens to 20 seconds — see `cadence()`.
const LIVE_MS = 120_000
const EVERY_MS = 20_000
const SLOWEST_MS = 120_000

let state: NotificationState = { unread: 0, notes: [], reading: false }
let timer = 0
let delay = EVERY_MS
let started = false
// Whether anybody is signed in, and which session this is. The epoch is what
// stops a response that was in flight when somebody signed out from landing in
// the next person's panel.
let signedIn = true
let epoch = 0
// Job updates come down the same socket. They are not notifications and never
// enter the panel; they are how the pad learns a step finished in the same
// second rather than up to a poll later.
const jobWatchers = new Set<JobWatcher>()
let socket: WebSocket | null = null
let socketDelay = 1_000
let live = false
const listeners = new Set<(state: NotificationState) => void>()

const cadence = () => (live ? LIVE_MS : EVERY_MS)

function publish(next: Partial<NotificationState>): void {
  state = { ...state, ...next }
  listeners.forEach((listen) => listen(state))
}

async function pull(): Promise<void> {
  if (!signedIn) return
  const mine = epoch
  try {
    const mail = await listNotifications({ limit: 30 })
    if (!signedIn || mine !== epoch) return
    delay = EVERY_MS
    publish({ unread: mail.unread || 0, notes: mail.notes || [] })
  } catch {
    // Signed out, offline, or the API is restarting. Back off rather than
    // hammering, and keep showing whatever was last true.
    delay = Math.min(delay * 2, SLOWEST_MS)
  }
}

function schedule(): void {
  window.clearTimeout(timer)
  // Nothing arrives for a tab nobody is looking at, and the tab catches up the
  // moment it comes back — see the visibility listener below.
  if (document.hidden || !signedIn) return
  timer = window.setTimeout(async () => {
    await pull()
    schedule()
  }, Math.max(delay, cadence()))
}

/**
 * Open the push channel, and keep it open.
 *
 * The token goes in the first frame because a browser cannot set headers on a
 * WebSocket, and the alternative — the token in the query string — would write
 * a live session into the server's access log and the browser's history. That
 * is the same trade this app already refused for file downloads.
 *
 * Every failure here is quiet: the poll below is still running, so a socket
 * that will not open costs latency and nothing else.
 */
function connect(): void {
  if (!signedIn || document.hidden) return
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING))
    return

  const mine = epoch
  let opened: WebSocket
  try {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    opened = new WebSocket(`${scheme}://${window.location.host}/api/notifications/stream`)
  } catch {
    return
  }
  socket = opened

  opened.onopen = () => {
    opened.send(JSON.stringify({ token: accessToken(), workspace: currentWorkspace() }))
  }

  opened.onmessage = (event) => {
    if (mine !== epoch) return
    let payload: StreamFrame
    try {
      payload = JSON.parse(event.data)
    } catch {
      return
    }
    if (payload.beat) return
    if (payload.error) {
      // Refused: not signed in, or not on this team. The poll will say the
      // same thing in its own way; there is nothing to retry here.
      live = false
      opened.close()
      return
    }
    if (payload.ready) {
      live = true
      socketDelay = 1_000
      publish({ unread: payload.unread || 0 })
      // One read on connect, so a socket that opened after something happened
      // still shows what it missed.
      pull()
      return
    }
    if (payload.job) {
      jobWatchers.forEach((watch) => {
        try {
          watch(payload.job)
        } catch {
          /* one bad listener must not stop the rest */
        }
      })
      return
    }
    if (payload.note) {
      publish({
        unread: payload.unread ?? state.unread + 1,
        notes: [payload.note, ...state.notes.filter((note) => note.id !== payload.note.id)].slice(
          0,
          30,
        ),
      })
    }
  }

  const dropped = () => {
    if (socket === opened) socket = null
    live = false
    if (!signedIn || mine !== epoch) return
    // Backing off rather than reconnecting hard: a server that is restarting
    // does not need a browser hammering the upgrade.
    window.setTimeout(connect, socketDelay)
    socketDelay = Math.min(socketDelay * 2, 30_000)
  }

  opened.onclose = dropped
  opened.onerror = dropped
}

/** Begin polling. Safe to call from every mount; only the first one starts it. */
export function watchNotifications(): void {
  if (started) return
  started = true

  pull().then(schedule)
  connect()

  const wake = () => {
    if (document.hidden || !signedIn) {
      window.clearTimeout(timer)
      return
    }
    delay = EVERY_MS
    connect()
    pull().then(schedule)
  }

  document.addEventListener('visibilitychange', wake)
  window.addEventListener('focus', wake)

  // A signed-out tab must not sit in a 401 loop, and must not keep somebody
  // else's mail on screen.
  onSession((session) => {
    epoch += 1
    signedIn = Boolean(session)
    if (socket) {
      // The old session's socket belongs to the old session.
      try {
        socket.close()
      } catch {
        /* already gone */
      }
      socket = null
      live = false
    }
    if (signedIn) {
      wake()
      return
    }
    // Signed out: stop, and clear the panel. Leaving one person's mail on
    // screen for the next one is the failure worth designing against.
    window.clearTimeout(timer)
    publish({ unread: 0, notes: [] })
  })
}

export function notificationsNow(): NotificationState {
  return state
}

/** Mark everything up to the newest note read. Sent when the panel closes. */
export async function markRead(): Promise<void> {
  const newest = state.notes[0]?.at
  if (!newest || state.unread === 0) return
  try {
    const mail = await markNotificationsRead({ through: newest })
    publish({ unread: mail.unread || 0, notes: mail.notes || [] })
  } catch {
    /* the next pull will show the truth */
  }
}

/** Drop what has been read. Unread notes stay — clearing is not reading. */
export async function clearSeen(): Promise<void> {
  try {
    const mail = await clearNotifications()
    publish({ unread: mail.unread || 0, notes: mail.notes || [] })
  } catch {
    /* nothing worth interrupting anybody for */
  }
}

export function useNotifications(): NotificationState {
  const [mail, setMail] = useState(state)

  useEffect((): (() => void) => {
    watchNotifications()
    const listen = (next: NotificationState) => setMail(next)
    listeners.add(listen)
    setMail(state)
    return () => listeners.delete(listen)
  }, [])

  return mail
}

/**
 * Follow job progress as it is pushed, rather than as it is polled.
 *
 * Returns the unsubscribe. The poll in `useJobWatch` stays exactly as it was:
 * a socket is a way of being early, never the only way of being told.
 */
export function onJobUpdate(watch: JobWatcher): () => void {
  watchNotifications()
  jobWatchers.add(watch)
  return () => jobWatchers.delete(watch)
}
