import type { AuthConfig } from '../types'
import { setCurrentWorkspace } from './workspace'

/**
 * Signing in, and staying signed in.
 *
 * The server is the authority on whether accounts exist here. It answers
 * `/api/auth/config` without a token — that is the one call a signed-out client
 * is allowed to make — and says whether it verifies tokens and which ways in
 * exist. Configuring the client from the server means the two halves cannot
 * disagree: an install with no sign-in set shows no sign-in screen, because the
 * API would let everyone in anyway and a login that guards nothing is a lie
 * told in an interface.
 *
 * Sign-in itself goes through PRISM's own API, which asks Zitadel. Every way in
 * either finishes at once (`step: 'done'`, with tokens) or asks for the code
 * Zitadel just emailed (`step: 'code'`, with the flow that code finishes). This
 * file holds the current session and the small set of calls the screens need;
 * nothing here decides what a user may do, because that is the server's answer
 * to give.
 *
 * The session is kept in localStorage so a reload stays signed in, and shared
 * between tabs through the `storage` event so signing out in one tab signs out
 * all of them. The access token lasts an hour and is traded for a fresh one a
 * minute before it runs out.
 */

/** Who is signed in. `provider` is how: `email`, `google`, `facebook`. */
export type User = {
  id: string
  email: string
  provider: string
  /** When this browser signed in, as an ISO date. */
  signed_in_at?: string
}

export type Session = {
  access_token: string
  refresh_token: string
  /** Milliseconds since the epoch. */
  expires_at: number
  user: User
}

/** What a way in answered: signed in, or a code to type. */
export type AuthStep =
  | { step: 'done' }
  | { step: 'code'; flow: string; email: string; purpose: CodePurpose }

/** Why a code was sent, which decides what the code screen says and asks for. */
export type CodePurpose = 'code' | 'signup' | 'reset' | 'confirm' | 'provider'

/** A refusal from the sign-in API: its stable `code` and its own sentence. */
export class AuthFailure extends Error {
  code: string
  status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

/** Called with the session that is now current, or null when there is none. */
type SessionListener = (session: Session | null) => void

const STORAGE_KEY = 'prism.session'
/** Refresh this long before the access token expires. */
const REFRESH_EARLY_MS = 60_000

const CLOSED: AuthConfig = { required: false, ready: false, providers: [], password_rules: null }

let settings: AuthConfig | null = null
let session: Session | null = null
let restored = false
let refreshTimer: number | undefined
let refreshing: Promise<Session | null> | null = null
let pendingStep: AuthStep | null = null
const listeners = new Set<SessionListener>()

/** What the server says about accounts. Read once, cached for the session. */
export async function authConfig(): Promise<AuthConfig> {
  if (settings) return settings
  try {
    const response = await fetch('/api/auth/config', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
    // The assertion is the API contract taken at its word: this is the one
    // endpoint answered without a token, and the shape is `AuthConfig` or the
    // fallback below. Nothing validates it at runtime, here or before.
    settings = response.ok ? ((await response.json()) as AuthConfig) : CLOSED
  } catch {
    // The API being unreachable is not the same as it being open, but a
    // sign-in screen that cannot reach its own server helps nobody. The
    // screens report the connection failure on their own.
    settings = CLOSED
  }
  return settings
}

// --- the stored session --------------------------------------------------------

function read(): Session | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const found = JSON.parse(raw) as Session
    return found?.access_token && found?.refresh_token && found?.user?.id ? found : null
  } catch {
    return null
  }
}

function write(next: Session | null): void {
  try {
    if (next) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    else window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // A private window or blocked storage: the session lives in memory for
    // this tab only, and a reload signs out. Nothing else breaks.
  }
}

function schedule(): void {
  window.clearTimeout(refreshTimer)
  if (!session) return
  const wait = Math.max(0, session.expires_at - REFRESH_EARLY_MS - Date.now())
  refreshTimer = window.setTimeout(() => void refresh(), wait)
}

function setSession(next: Session | null, persist = true): void {
  session = next
  if (persist) write(next)
  schedule()
  listeners.forEach((listen) => listen(session))
}

type TokenAnswer = {
  step: string
  flow?: string
  email?: string
  purpose?: string
  access_token?: string
  refresh_token?: string
  expires_in?: number
  user?: { id: string; email: string; provider?: string } | null
}

function fromAnswer(answer: TokenAnswer): Session {
  return {
    access_token: answer.access_token || '',
    refresh_token: answer.refresh_token || '',
    expires_at: Date.now() + (answer.expires_in || 3600) * 1000,
    user: {
      id: answer.user?.id || '',
      email: answer.user?.email || '',
      provider: answer.user?.provider || 'email',
      signed_in_at: session?.user.signed_in_at || new Date().toISOString(),
    },
  }
}

async function post(path: string, body: object): Promise<TokenAnswer> {
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
    })
  } catch {
    throw new AuthFailure('Could not reach PRISM’s server.', 'NETWORK', 0)
  }
  let data: Record<string, unknown> = {}
  try {
    data = (await response.json()) as Record<string, unknown>
  } catch {
    // An empty or non-JSON body. The status says enough below.
  }
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : ''
    const code = typeof data.code === 'string' ? data.code : `HTTP_${response.status}`
    throw new AuthFailure(detail || 'That did not work.', code, response.status)
  }
  return data as TokenAnswer
}

/** Turn an answer into a step, signing in when it is `done`. */
function settle(answer: TokenAnswer): AuthStep {
  if (answer.step === 'done') {
    const next = fromAnswer(answer)
    next.user.signed_in_at = new Date().toISOString()
    setSession(next)
    return { step: 'done' }
  }
  return {
    step: 'code',
    flow: answer.flow || '',
    email: answer.email || '',
    purpose: (answer.purpose || 'code') as CodePurpose,
  }
}

/**
 * Trade the refresh token for a new pair. Concurrent callers share one trip.
 * A refusal signs out; the server being unreachable keeps the session, which
 * may still have minutes left and will be retried.
 */
async function refresh(): Promise<Session | null> {
  if (!session) return null
  if (refreshing) return refreshing
  const token = session.refresh_token
  refreshing = (async () => {
    try {
      const answer = await post('/api/auth/refresh', { refresh_token: token })
      if (answer.step === 'done') setSession(fromAnswer(answer))
      return session
    } catch (failure) {
      if (failure instanceof AuthFailure && failure.status === 401) {
        setSession(null)
        setCurrentWorkspace('')
      } else {
        // Try again shortly rather than at the (already passed) early mark.
        window.clearTimeout(refreshTimer)
        refreshTimer = window.setTimeout(() => void refresh(), 30_000)
      }
      return session
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

function wakeUp(): void {
  // A laptop that slept through the refresh timer wakes with an expired token.
  if (session && session.expires_at - REFRESH_EARLY_MS <= Date.now()) void refresh()
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY) return
    // Another tab signed in, refreshed, or signed out. Follow it without
    // writing back, which would echo the event to every other tab.
    setSession(read(), false)
  })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') wakeUp()
  })
  window.addEventListener('focus', wakeUp)
}

/** The access token to send with an API call, or '' when there is none. */
export function accessToken(): string {
  return session?.access_token || ''
}

export function currentUser(): User | null {
  return session?.user || null
}

/** Called whenever the session changes. Returns the unsubscribe. */
export function onSession(listen: SessionListener): () => void {
  listeners.add(listen)
  return () => listeners.delete(listen)
}

/** Whether this install requires anybody to sign in at all. */
export async function signInRequired(): Promise<boolean> {
  const found = await authConfig()
  return Boolean(found.required)
}

/** The session this browser kept, refreshed first when it is about to run out. */
export async function startSession(): Promise<Session | null> {
  if (!restored) {
    restored = true
    session = read()
    schedule()
  }
  if (session && session.expires_at - REFRESH_EARLY_MS <= Date.now()) await refresh()
  return session
}

/**
 * The step a provider return left waiting for the sign-in screen, once.
 * Set when Google or Facebook came back with an email it had not proven, so
 * the screen opens on the code it was sent.
 */
export function takePendingStep(): AuthStep | null {
  const found = pendingStep
  pendingStep = null
  return found
}

// --- the ways in --------------------------------------------------------------

export async function signInWithPassword(email: string, password: string): Promise<AuthStep> {
  return settle(await post('/api/auth/sign-in', { email, password }))
}

/** A new account. Always a code first: the password is set once it is typed. */
export async function signUpWithPassword(email: string, password: string): Promise<AuthStep> {
  return settle(await post('/api/auth/sign-up', { email, password }))
}

/** A six-digit code by email — the kit's "one more step" screen. */
export async function sendEmailCode(email: string): Promise<AuthStep> {
  return settle(await post('/api/auth/code', { email }))
}

/** A code to set a new password with. Answers the same for any address. */
export async function startReset(email: string): Promise<AuthStep> {
  return settle(await post('/api/auth/reset', { email }))
}

/** Another code for the same flow. The earlier one stops working. */
export async function resendCode(flow: string): Promise<AuthStep> {
  return settle(await post('/api/auth/code/resend', { flow }))
}

/** Check the code — with the new password, when the flow is a reset. */
export async function verifyEmailCode(flow: string, code: string, password = ''): Promise<AuthStep> {
  return settle(await post('/api/auth/code/verify', { flow, code, password }))
}

/** Google or Facebook: leaves the page for the provider, via Zitadel. */
export async function signInWithProvider(provider: string): Promise<void> {
  const answer = (await post('/api/auth/provider', { provider })) as { url?: string }
  if (!answer.url) throw new AuthFailure('That sign-in method isn’t available.', 'UNKNOWN_PROVIDER', 400)
  window.location.assign(answer.url)
}

/**
 * Finish a provider return (`lib/oauthReturn.ts` captured its id and token).
 * Signs in, or leaves a code step for the sign-in screen to open on.
 */
export async function finishProviderReturn(id: string, token: string): Promise<void> {
  const step = settle(await post('/api/auth/provider/finish', { id, token }))
  if (step.step === 'code') pendingStep = step
}

export async function signOut(): Promise<void> {
  // PRISM's tokens are its own and stateless: forgetting them is signing out.
  setSession(null)
  // The chosen workspace is a property of the person, not of the browser. Left
  // behind, the next person to sign in on this machine starts inside a book of
  // work they are not on the team of - every screen answers 403 and the app
  // looks broken rather than simply not theirs.
  setCurrentWorkspace('')
}
