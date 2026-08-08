/**
 * What Google or Facebook said on the way back, when the answer was no.
 *
 * A provider sign-in is not a request that fails in place - it navigates the
 * whole browser away, and whatever went wrong comes back as parameters on the
 * URL of a fresh page load. Nothing in this app was reading them, so every
 * failure looked identical from the outside: the provider's screen, a redirect,
 * and then PRISM's sign-in card again with no explanation and the person's own
 * guess as to why. "It just goes back to the login page" is the bug this file
 * exists to end.
 *
 * READ SYNCHRONOUSLY AT MODULE LOAD, and imported before anything creates the
 * Supabase client. That ordering is the whole design:
 *
 *   - `detectSessionInUrl: true` means supabase-js parses this same URL itself
 *     and strips it once it has. It creates its client lazily, behind an
 *     `await` on `/api/auth/config`, so the race is not tight - but it is a
 *     race, and losing it means the error is gone before any component asks.
 *     Capturing at import time cannot lose.
 *   - The capture also CLEARS the parameters, so a reload does not re-announce
 *     a failure the person already read and acted on.
 *
 * WHY THE HASH AND NOT THE QUERY STRING. `@supabase/auth-js` ships
 * `flowType: 'implicit'` by default and this app does not override it, so
 * errors arrive in the fragment - `#error=access_denied&error_code=...`. That
 * collides with PRISM's own hash routing, which is exactly why the failure was
 * invisible: `#error=...` matches no route, so the router quietly fell through
 * to the signed-out screen. Both places are read anyway, because the day
 * somebody sets `flowType: 'pkce'` the same errors move to the query string,
 * and a file that only looked in one of them would go silent again without a
 * single test failing.
 */

export type OAuthReturn = {
  /** GoTrue's `error_code` when it sent one, else its coarser `error`. */
  code: string
  /** The provider's own sentence, URL-decoded. May be empty. */
  message: string
}

function readFrom(raw: string): OAuthReturn | null {
  if (!raw) return null
  // A hash carrying a route (`#/pad/abc`) is not a failure report. Only the
  // presence of an `error` key makes this ours to read.
  const params = new URLSearchParams(raw.replace(/^[#?]/, ''))
  const error = (params.get('error') || '').trim()
  const code = (params.get('error_code') || '').trim()
  if (!error && !code) return null
  return {
    // `error_code` is the specific one (`provider_email_needs_verification`);
    // `error` is the OAuth-level bucket (`access_denied`, `server_error`).
    // Prefer the specific, fall back to the bucket.
    code: code || error,
    message: (params.get('error_description') || '').trim(),
  }
}

function capture(): OAuthReturn | null {
  if (typeof window === 'undefined') return null

  const fromHash = readFrom(window.location.hash || '')
  const fromQuery = readFrom(window.location.search || '')
  const found = fromHash || fromQuery
  if (!found) return null

  // Put the URL back the way somebody would want to bookmark it. `replaceState`
  // rather than assigning `location.hash`, which would push a history entry and
  // make Back walk through the failure again.
  try {
    const clean = window.location.pathname + (fromQuery ? '' : window.location.search)
    window.history.replaceState(null, '', clean || '/')
  } catch {
    // Some embedded browsers refuse `replaceState` on a file: origin. The
    // message still gets shown; it just survives a reload. Not worth failing
    // the whole boot over.
  }

  return found
}

let pending: OAuthReturn | null = capture()

/**
 * The failure a provider sent us back with, once.
 *
 * Consumed rather than merely read: the sign-in screen can mount more than once
 * in a session (sign out, come back) and an error from a redirect twenty
 * minutes ago is not news the second time.
 */
export function takeOAuthReturn(): OAuthReturn | null {
  const found = pending
  pending = null
  return found
}
