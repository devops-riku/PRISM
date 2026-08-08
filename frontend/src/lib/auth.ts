import { createClient } from '@supabase/supabase-js'
import type { Provider, Session, SupabaseClient, User } from '@supabase/supabase-js'
import type { AuthConfig } from '../types'
import { finishSessionReturn } from './oauthReturn'
import { setCurrentWorkspace } from './workspace'

/**
 * Signing in, and staying signed in.
 *
 * The server is the authority on whether accounts exist here. It answers
 * `/api/auth/config` without a token — that is the one call a signed-out client
 * is allowed to make — and says whether it verifies tokens and which Supabase
 * project to sign in to. Configuring the client from the server means the two
 * halves cannot disagree: an install with no project set shows no sign-in
 * screen, because the API would let everyone in anyway and a login that guards
 * nothing is a lie told in an interface.
 *
 * Sign-in itself is Supabase's. This file holds the client, the current session
 * and the small set of calls the screens need; nothing here decides what a user
 * may do, because that is the server's answer to give.
 */

/** Called with the session that is now current, or null when there is none. */
type SessionListener = (session: Session | null) => void

let client: SupabaseClient | null = null
let settings: AuthConfig | null = null
let session: Session | null = null
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
    settings = response.ok
      ? ((await response.json()) as AuthConfig)
      : { required: false, url: '', anon_key: '' }
  } catch {
    // The API being unreachable is not the same as it being open, but a
    // sign-in screen that cannot reach its own project helps nobody. The
    // screens report the connection failure on their own.
    settings = { required: false, url: '', anon_key: '' }
  }
  return settings
}

/** The Supabase client, or null when this install has no accounts. */
export async function supabase(): Promise<SupabaseClient | null> {
  if (client) return client
  const found = await authConfig()
  const url: string = (found.url || import.meta.env.VITE_SUPABASE_URL || '').trim()
  const key: string = (found.anon_key || import.meta.env.VITE_SUPABASE_ANON_KEY || '').trim()
  if (!url || !key) return null

  client = createClient(url, key, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  })

  // `getSession()` waits for URL initialisation but does not expose an error
  // raised while exchanging a PKCE code. `initialize()` returns that result,
  // and calling it here only awaits the promise the constructor already
  // started. Keep callback credentials in place when that exchange fails so a
  // reload can retry them.
  const { error: initializationError } = await client.auth.initialize()
  const { data, error } = await client.auth.getSession()
  session = data?.session || null
  // Supabase's implicit callback uses the fragment for its tokens, the same
  // place PRISM uses for routes. Only after getSession has parsed/exchanged
  // those credentials is it safe to replace them with the private `#/`
  // entry point. If session initialisation reports an error, leave the
  // callback intact so a reload can retry its one-time credentials.
  if (session && !initializationError && !error) finishSessionReturn()
  client.auth.onAuthStateChange((_event, next) => {
    session = next || null
    listeners.forEach((listen) => listen(session))
  })
  return client
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

export async function startSession(): Promise<Session | null> {
  const supa = await supabase()
  if (!supa) return null
  const { data } = await supa.auth.getSession()
  session = data?.session || null
  return session
}

// --- the ways in --------------------------------------------------------------

export async function signInWithPassword(email: string, password: string) {
  const supa = await supabase()
  if (!supa) throw new Error('This install has no sign-in configured.')
  const { data, error } = await supa.auth.signInWithPassword({ email, password })
  if (error) throw error
  session = data.session
  return data
}

export async function signUpWithPassword(email: string, password: string) {
  const supa = await supabase()
  if (!supa) throw new Error('This install has no sign-in configured.')
  const { data, error } = await supa.auth.signUp({
    email,
    password,
    // Where the confirmation link comes back to. Without it Supabase uses the
    // project's Site URL, which ships pointing at localhost:3000 - a port this
    // app has never run on, so the email arrives with a link to nothing. The
    // origin is whatever the person is actually using, dev or deployed, and it
    // still has to be on the project's redirect allow-list.
    options: { emailRedirectTo: window.location.origin },
  })
  if (error) throw error
  session = data.session
  return data
}

/** A six-digit code by email — the kit's "one more step" screen. */
export async function sendEmailCode(email: string): Promise<void> {
  const supa = await supabase()
  if (!supa) throw new Error('This install has no sign-in configured.')
  const { error } = await supa.auth.signInWithOtp({
    email,
    // The six digits and a clickable link are the same email in Supabase: the
    // Magic Link template decides which one arrives. This screen asks for the
    // digits ({{ .Token }} in that template), and `emailRedirectTo` covers the
    // case where the template still sends a link as well.
    options: { shouldCreateUser: true, emailRedirectTo: window.location.origin },
  })
  if (error) throw error
}

/**
 * Check the six digits.
 *
 * TWO TYPES ARE TRIED, and the reason is that the send path decides which one
 * is right. `sendEmailCode` passes `shouldCreateUser: true`, so an address
 * that is not yet a user gets GoTrue's SIGNUP confirmation token, while an
 * existing user gets a plain email OTP. The digits look identical and the
 * verify call does not: `type: 'email'` against a signup token is rejected
 * with "Token has expired or is invalid" - the same sentence a genuinely
 * expired code produces, which is why this failed in a way that read as
 * expiry on a code that was seconds old.
 *
 * The first error is the one re-thrown, not the second. If both fail the
 * interesting reason is almost always the first one; the fallback failing
 * only tells you it was not a signup token either.
 */
export async function verifyEmailCode(email: string, code: string) {
  const supa = await supabase()
  if (!supa) throw new Error('This install has no sign-in configured.')

  const first = await supa.auth.verifyOtp({ email, token: code, type: 'email' })
  if (!first.error) {
    session = first.data.session
    return first.data
  }

  const second = await supa.auth.verifyOtp({ email, token: code, type: 'signup' })
  if (second.error) throw first.error
  session = second.data.session
  return second.data
}

export async function sendResetLink(email: string): Promise<void> {
  const supa = await supabase()
  if (!supa) throw new Error('This install has no sign-in configured.')
  const { error } = await supa.auth.resetPasswordForEmail(email, {
    redirectTo: window.location.origin,
  })
  if (error) throw error
}

/** Google or Facebook. The provider has to be enabled in Supabase first. */
export async function signInWithProvider(provider: Provider): Promise<void> {
  const supa = await supabase()
  if (!supa) throw new Error('This install has no sign-in configured.')
  const { error } = await supa.auth.signInWithOAuth({
    provider,
    // Provider OAuth must start with a fragment-free return URL. The recent
    // public-landing refactor sent `/#/` here, which changed a previously
    // working provider request and made Meta reject it. Supabase writes its
    // own success/error fragment on return; `oauthReturn.ts` consumes that
    // first and only then cleans the browser URL to the private `/#/` route.
    options: { redirectTo: window.location.origin },
  })
  if (error) throw error
}

export async function signOut(): Promise<void> {
  const supa = await supabase()
  if (!supa) return
  await supa.auth.signOut()
  session = null
  // The chosen workspace is a property of the person, not of the browser. Left
  // behind, the next person to sign in on this machine starts inside a book of
  // work they are not on the team of - every screen answers 403 and the app
  // looks broken rather than simply not theirs.
  setCurrentWorkspace('')
}
