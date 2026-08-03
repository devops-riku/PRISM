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

/** None of these calls run a Gemini pass - the read, `/revise` and
 * `/finalize` are plain, synchronous state writes on the server - so one
 * timeout covers all of them. Fifteen seconds is generous for a request whose
 * body is a few hundred bytes and whose server-side work is a file write: past
 * that, something is wrong rather than slow, and saying so beats a spinner
 * that never resolves. */
const REQUEST_TIMEOUT_MS = 15_000

/**
 * `/submit`, and `/submit` alone, because it is the one call on this door that
 * carries files.
 *
 * The two numbers differ for a reason worth writing down rather than tuning by
 * feel. An `AbortController` here bounds the *whole* request, not the gap
 * between packets - there is no idle timeout in `fetch` - so a limit shorter
 * than the transfer aborts a request that is making perfectly good progress,
 * and the client is told "that took too long" about an upload that was three
 * quarters done. The server's own ceiling is
 * `config.MAX_CLIENT_UPLOAD_TOTAL_BYTES`, 20 MiB, so the question is how long
 * 20 MiB legitimately takes *upstream*, which is the direction consumer
 * connections are slowest in and the one nobody quotes: hotel wifi, a phone on
 * a train, a fixed line sold as "100 Mbps down". At 1 Mbps up - not a
 * pathological number, it is an ordinary bad afternoon - 20 MiB is about 170
 * seconds. Fifteen would abort every one of them.
 *
 * Three minutes, then, which covers that case with a little room and still
 * ends. It is deliberately not "no timeout": a request with no bound at all
 * leaves the form spinning forever when a proxy silently drops the connection,
 * which is the failure this constant exists to convert into a sentence.
 */
const UPLOAD_TIMEOUT_MS = 180_000

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
  /** How long this one call may take before it is abandoned. Defaults to
   * `REQUEST_TIMEOUT_MS`; only `/submit` passes anything else, and
   * `UPLOAD_TIMEOUT_MS`'s own comment is why. */
  timeoutMs?: number
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

  const { method = 'GET', body, signal, headers, timeoutMs = REQUEST_TIMEOUT_MS } = options
  const url = `${API_ROOT}/${encodeURIComponent(trimmedToken)}${suffix}`

  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
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

/** What `POST /api/client/{token}/submit` accepts, filled in once from
 * `issued`.
 *
 * `client_kind` is one of `KindPicker`'s own ids and the server checks it
 * against `kinds.BY_ID` rather than trusting it; `client_kind_label` is the
 * client's own word for their discipline and is read only when the kind is
 * `other`. Both land on their own `Intake` fields rather than inside the
 * studio's `preset` - see `intakes.Intake.client_kind` for why that
 * distinction is a security boundary and not a filing decision. */
export type ClientSubmitBody = {
  client_email: string
  client_phone: string
  scope: string
  budget_text: string
  client_kind: string
  client_kind_label: string
}

/**
 * What the client attached, already split the way the server's two file fields
 * are named.
 *
 * The split is made at pick time by `ClientDropzone`, not here and not on the
 * server: by the time a multipart body has been parsed, the order the person
 * chose their files in is gone and only the field name survives. Which field a
 * file arrives in is a *hint* the server does not trust - `_read_client_files`
 * walks both lists as one and decides what each file is from its own declared
 * type - so a picker that got the split wrong would be corrected rather than
 * obeyed. It is still worth sending correctly: it is the same shape the
 * studio's own upload routes use, so one picker's decision reads the same on
 * both sides.
 */
export type ClientSubmitFiles = {
  images: File[]
  documents: File[]
}

/** The client's own words and their own files, written once. Legal only from
 * `issued` - a second call, or one from any later state, gets the same opaque
 * refusal an unknown token would.
 *
 * Multipart, not JSON, since the route grew file fields: every text field is a
 * `Form(...)` on the server now, and a JSON body reaches it as a 422 with no
 * field it recognises. Nothing sets `Content-Type` here on purpose - `fetch`
 * derives it from the `FormData` body, including the boundary parameter that
 * makes the body parseable at all, and a hand-written
 * `multipart/form-data` header would arrive without one and take the whole
 * submission down. The other two writes on this door stay JSON; they have
 * nothing to attach.
 */
export async function submitClientIntake(
  token: string,
  body: ClientSubmitBody,
  files: ClientSubmitFiles = { images: [], documents: [] },
  options: CallOptions = {},
): Promise<ClientIntakeView> {
  const form = new FormData()
  // Written out one key at a time rather than looped over `Object.entries`,
  // for the reason `clientview.py` gives about its own dict literals: these
  // names are a contract with six `Form(...)` parameters in `main.py`, and a
  // loop would carry a seventh key there silently the day one is added to
  // `ClientSubmitBody` for some other purpose.
  form.append('client_email', body.client_email)
  form.append('client_phone', body.client_phone)
  form.append('scope', body.scope)
  form.append('budget_text', body.budget_text)
  form.append('client_kind', body.client_kind)
  form.append('client_kind_label', body.client_kind_label)

  // Repeated parts under one name, which is what `List[UploadFile]` reads. A
  // client with nothing to attach appends nothing at all: the server's two
  // fields default to `[]`, and an empty part with no filename is exactly the
  // shape `submit_client_intake`'s loose typing exists to survive rather than
  // something to go out of the way to send.
  files.images.forEach((file) => form.append('images', file))
  files.documents.forEach((file) => form.append('documents', file))

  return request<ClientIntakeView>(token, '/submit', {
    method: 'POST',
    body: form,
    timeoutMs: UPLOAD_TIMEOUT_MS,
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
