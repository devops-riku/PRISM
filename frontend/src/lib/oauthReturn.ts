/**
 * Coming back from Google or Facebook.
 *
 * A provider sign-in is not a request that fails or succeeds in place - it
 * navigates the whole browser away, and the answer comes back as parameters on
 * the URL of a fresh page load. Zitadel sends the browser to one of two
 * addresses PRISM's API named when the sign-in started:
 *
 *   - `/?signin=provider&id=…&token=…` when the provider said yes. The id and
 *     token are a one-time receipt the API trades for a session.
 *   - `/?signin=provider-failed…` when it said no, or the person cancelled.
 *
 * READ SYNCHRONOUSLY AT MODULE LOAD, and the URL is cleaned at the same moment
 * to the private `#/` entry point. The receipt is a credential for as long as
 * it is unspent: left in the address bar it would sit in history and in the
 * Referer of whatever loads next, so it is held here, in memory, instead. A
 * reload after that does not re-announce a failure the person already read.
 *
 * Without this file, every failure looked identical from the outside: the
 * provider's screen, a redirect, and then PRISM's sign-in card again with no
 * explanation and the person's own guess as to why. "It just goes back to the
 * login page" is the bug this file exists to end.
 */

export type OAuthReturn = {
  /** A stable code for `describeAuthError`. */
  code: string
  /** The provider's own sentence, when it sent one. May be empty. */
  message: string
}

type Receipt = { id: string; token: string }

/** The canonical private entry point, without callback parameters. */
function studioPath(): string {
  return `${window.location.pathname || '/'}#/`
}

/**
 * Replace a callback URL without adding a sensitive history entry, then tell
 * the hash router about the replaceState change (replaceState itself emits no
 * `hashchange` event).
 */
function replaceWithStudio(): void {
  window.history.replaceState(null, '', studioPath())
  window.dispatchEvent(new Event('hashchange'))
}

function capture(): { receipt: Receipt | null; failure: OAuthReturn | null } {
  if (typeof window === 'undefined') return { receipt: null, failure: null }
  const query = new URLSearchParams((window.location.search || '').replace(/^\?/, ''))
  const kind = query.get('signin') || ''
  if (kind !== 'provider' && kind !== 'provider-failed') return { receipt: null, failure: null }

  const id = (query.get('id') || '').trim()
  const token = (query.get('token') || '').trim()
  const said = (query.get('error_description') || query.get('error') || '').trim()

  try {
    replaceWithStudio()
  } catch {
    // Some embedded browsers refuse `replaceState` on a file: origin. The
    // sign-in still finishes; the URL just keeps its parameters.
  }

  if (kind === 'provider' && id && token) return { receipt: { id, token }, failure: null }
  return {
    receipt: null,
    failure: { code: kind === 'provider' ? 'PROVIDER_FAILED' : 'PROVIDER_CANCELLED', message: said },
  }
}

const captured = capture()
let receipt: Receipt | null = captured.receipt
let pending: OAuthReturn | null = captured.failure

/** Whether the root must mount AuthGate so a provider return can be finished. */
export function hasPendingSessionReturn(): boolean {
  return receipt !== null
}

/** The receipt a successful provider return carried, once. */
export function takeProviderReceipt(): Receipt | null {
  const found = receipt
  receipt = null
  return found
}

/** Hold a failure for the sign-in screen to show (finishing a receipt can fail too). */
export function rememberOAuthFailure(failure: OAuthReturn): void {
  pending = failure
}

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
