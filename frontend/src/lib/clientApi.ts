/**
 * The client's own door - `/api/client/{token}` and the three writes beside
 * it. A bare `fetch`, deliberately apart from `lib/api.ts`.
 *
 * This module imports neither `currentWorkspace` (from `lib/workspace.ts`)
 * nor `accessToken` (from `lib/auth.ts`), and it must never start to. That
 * is not an oversight to fix later - it is the entire point of this file
 * existing as its own module instead of a few more exports bolted onto
 * `lib/api.ts`. A client reading their own intake has no studio session and
 * no workspace of their own to send: the token in the URL *is* their whole
 * identity here, exactly as an invitation token is in `fetchInvite`. Import
 * `lib/api.ts`'s `request()` here, or its `currentWorkspace`/`accessToken`,
 * and the next call this module makes carries whichever studio happens to
 * be signed in on this browser - an `X-Workspace` header and a bearer token
 * that mean nothing to an anonymous door, attached to a request from a
 * stranger holding a link, which is exactly the confusion a *separate*
 * client-facing module exists to make structurally impossible rather than
 * merely avoid by habit.
 *
 * Every answer here is a `ClientIntakeView` from `../types` - `clientview.of`'s
 * output, unfiltered further. There is nothing to assert about its shape the
 * way `lib/api.ts` asserts a bundle or a job: the server already built this
 * dict by hand, field by field, for exactly this door.
 */

import type { ClientIntakeView } from '../types'

const API_ROOT = '/api/client'

/** What every call takes: a way for the caller to give up on it. */
type CallOptions = { signal?: AbortSignal }

/** None of these calls run a Gemini pass - `/submit`, `/revise` and
 * `/finalize` are plain, synchronous state writes on the server - so one
 * timeout covers reads and writes alike. */
const REQUEST_TIMEOUT_MS = 15_000

/** Which way a call to this door failed. What a caller branches on. */
export type ClientApiErrorKind = 'network' | 'http' | 'parse' | 'timeout' | 'aborted' | 'validation'

type ClientApiErrorOptions = {
  status?: number
  kind?: ClientApiErrorKind
  detail?: string
  cause?: unknown
}

/** A failed call to the client's own door. */
export class ClientApiError extends Error {
  /** HTTP status, or 0 when the request never landed. */
  status: number
  kind: ClientApiErrorKind
  /** The server's own `detail`, verbatim, when it sent one. */
  detail: string

  constructor(
    message: string,
    { status = 0, kind = 'http', detail = '', cause }: ClientApiErrorOptions = {},
  ) {
    super(message)
    this.name = 'ClientApiError'
    this.status = status
    this.kind = kind
    this.detail = detail
    if (cause !== undefined) this.cause = cause
  }

  /**
   * Every refusal this door gives - an unknown token, an expired one, a
   * closed intake, or a write attempted from the wrong state - answers with
   * the identical opaque 404. See `_CLIENT_LINK_GONE` in `backend/app/main.py`.
   */
  get isGone(): boolean {
    return this.status === 404
  }

  /** The courtesy limiter on `/submit`, `/revise` and `/finalize` - twenty
   * writes per address per route per minute. Not the same "no" as `isGone`:
   * the link still works, this caller just needs to wait. */
  get isRateLimited(): boolean {
    return this.status === 429
  }

  /** The API could not be reached at all. */
  get isNetwork(): boolean {
    return this.kind === 'network'
  }
}

/** An error body, as far as this file reads it. Both fields stay unchecked -
 * same idiom as `lib/api.ts`'s `extractDetail`. */
type ErrorBody = { detail?: unknown }

/** Pull the message out of an error response body. FastAPI sends
 * `{"detail": "..."}` for every refusal on this door; a validation failure
 * (a malformed JSON body reaching `/submit` or `/revise`) sends
 * `{"detail": [{loc, msg, type}, ...]}` instead, which is why this still
 * checks for an array rather than assuming a string.
 *
 * @returns '' when the body carries nothing worth showing
 */
async function extractDetail(response: Response): Promise<string> {
  let body = ''
  try {
    body = await response.text()
  } catch {
    return ''
  }
  if (!body) return ''

  try {
    const data = JSON.parse(body) as ErrorBody | null
    const detail = data?.detail

    if (typeof detail === 'string' && detail.trim()) return detail.trim()

    if (Array.isArray(detail)) {
      const messages = detail
        .map((entry: unknown) => {
          if (typeof entry === 'string') return entry
          if (entry && typeof entry === 'object' && 'msg' in entry) {
            const msg = (entry as { msg?: unknown }).msg
            return typeof msg === 'string' ? msg : ''
          }
          return ''
        })
        .filter(Boolean)
      if (messages.length) return messages.join('; ')
    }
  } catch {
    // Not JSON. Fall through.
  }

  const text = body.trim()
  if (text && text.length <= 300 && !text.startsWith('<')) return text
  return ''
}

async function httpError(response: Response): Promise<ClientApiError> {
  const detail = await extractDetail(response)
  const message =
    detail || `That link answered ${response.status}${response.statusText ? ` ${response.statusText}` : ''}.`
  return new ClientApiError(message, { status: response.status, kind: 'http', detail })
}

type RequestOptions = {
  method?: string
  body?: BodyInit
  signal?: AbortSignal
  headers?: Record<string, string>
}

/**
 * One call to `${API_ROOT}/{token}${suffix}`. No `X-Workspace`, no
 * `Authorization` - see this file's own docstring for why that is not
 * negotiable here.
 */
async function request<T>(token: string, suffix: string, options: RequestOptions = {}): Promise<T> {
  const trimmedToken = String(token ?? '').trim()
  if (!trimmedToken) {
    throw new ClientApiError('No link to read - the token is empty.', { kind: 'validation' })
  }

  const { method = 'GET', body, signal, headers } = options
  const url = `${API_ROOT}/${encodeURIComponent(trimmedToken)}${suffix}`

  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, REQUEST_TIMEOUT_MS)
  const forwardAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', forwardAbort, { once: true })
  }

  let response: Response
  try {
    response = await fetch(url, {
      method,
      body,
      signal: controller.signal,
      headers: { Accept: 'application/json', ...(headers || {}) },
      cache: 'no-store',
    })
  } catch (cause) {
    const name = cause && typeof cause === 'object' && 'name' in cause ? cause.name : undefined
    if (name === 'AbortError') {
      if (timedOut) {
        throw new ClientApiError('That took too long to answer. Try again.', {
          kind: 'timeout',
          cause,
        })
      }
      throw new ClientApiError('Cancelled.', { kind: 'aborted', cause })
    }
    throw new ClientApiError('Could not be reached. Check the connection and try again.', {
      kind: 'network',
      cause,
    })
  } finally {
    clearTimeout(timer)
    if (signal) signal.removeEventListener('abort', forwardAbort)
  }

  if (!response.ok) throw await httpError(response)

  let text: string
  try {
    text = await response.text()
  } catch (cause) {
    throw new ClientApiError('That answer was cut off before it finished. Try again.', {
      status: response.status,
      kind: 'parse',
      cause,
    })
  }

  try {
    return JSON.parse(text) as T
  } catch (cause) {
    throw new ClientApiError('That did not come back as something this page understands.', {
      status: response.status,
      kind: 'parse',
      cause,
    })
  }
}

/** What a client's own link shows right now - `GET /api/client/{token}`. */
export async function fetchClientIntake(
  token: string,
  options: CallOptions = {},
): Promise<ClientIntakeView> {
  return request<ClientIntakeView>(token, '', { signal: options.signal })
}

/** The four fields `POST /api/client/{token}/submit` accepts, filled in once
 * from `issued`. */
export type ClientSubmitBody = {
  client_email: string
  client_phone: string
  scope: string
  budget_text: string
}

/** The client's own words, written once. Legal only from `issued` - a
 * second call, or one from any later state, gets the same opaque refusal an
 * unknown token would. */
export async function submitClientIntake(
  token: string,
  body: ClientSubmitBody,
  options: CallOptions = {},
): Promise<ClientIntakeView> {
  return request<ClientIntakeView>(token, '/submit', {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
}

/** Ask for a change, in the client's own words. Legal only from `sent`. */
export async function reviseClientIntake(
  token: string,
  asked: string,
  options: CallOptions = {},
): Promise<ClientIntakeView> {
  return request<ClientIntakeView>(token, '/revise', {
    method: 'POST',
    body: JSON.stringify({ asked }),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
}

/** Accept what was sent. Not a signature and nothing is charged - that
 * framing belongs on the screen that calls this, not here. Legal only from
 * `sent`. No body. */
export async function finalizeClientIntake(
  token: string,
  options: CallOptions = {},
): Promise<ClientIntakeView> {
  return request<ClientIntakeView>(token, '/finalize', {
    method: 'POST',
    signal: options.signal,
  })
}

/** Which of a client's two document formats a link can ask for. */
export type ClientQuotationExtension = 'html' | 'pdf'

/**
 * The URL of the one document a client may read - `GET
 * /api/client/{token}/quotation.{html,pdf}`. A plain string, not a fetch: an
 * `<a href>` needs no header at all on this anonymous door, unlike
 * `lib/api.ts`'s `openFile`, which exists solely to attach a session's
 * bearer token no anonymous request has to carry in the first place.
 */
export function clientQuotationUrl(token: string, ext: ClientQuotationExtension): string {
  const trimmedToken = String(token ?? '').trim()
  if (!trimmedToken) throw new TypeError('clientQuotationUrl needs a token.')
  return `${API_ROOT}/${encodeURIComponent(trimmedToken)}/quotation.${ext}`
}
