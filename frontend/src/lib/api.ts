/**
 * The only place in the client that talks to the API.
 *
 * Every request goes to a same-origin `/api/...` path. In development Vite
 * proxies that to http://localhost:8000 (see vite.config.js); in production the
 * bundle is served from the same origin as the API. Either way the browser
 * never sees the Gemini key and never negotiates CORS.
 *
 * Errors are the point of this module. Every failure surfaces as an `ApiError`
 * carrying a `kind` you can branch on and a message that says what happened and
 * what to do next — never an apology, never a raw stack trace.
 */

import { CURRENCIES } from './currencies'
import { accessToken } from './auth'
import { currentWorkspace, setCurrentWorkspace } from './workspace'
import type {
  Currency,
  DocumentKind,
  Intake,
  IntakeIssued,
  IntakeRevision,
  Invite,
  InvitePreview,
  Job,
  Mailbox,
  Member,
  MemberRole,
  ProposalBundle,
  ProposalDocument,
  ProposalDocumentSummary,
  ProposalSummary,
  StudioDefaults,
  Team,
  Workspace,
} from '../types'

const API_ROOT = '/api'

/** What every call takes: a way for the caller to give up on it. */
type CallOptions = { signal?: AbortSignal }

/**
 * Which workspace this browser is looking at.
 *
 * Workspaces are separate books of work - separate settings, rates, terms,
 * numbering and files - and the server keeps them apart. Which one is in play
 * is the client's to say, so it travels on every request as a header, and on
 * file links as a query parameter because an `<a href>` cannot carry a header.
 *
 * There is no login behind this. It decides which book you are reading, not
 * what you are allowed to read.
 */
const WORKSPACE_HEADER = 'X-Workspace'
const WORKSPACE_PARAM = 'workspace'

// Re-exported from here because every screen already asks `lib/api` for them.
// The value itself lives in `lib/workspace`, so that signing out can clear it
// without importing this module and closing a cycle.
export { currentWorkspace, setCurrentWorkspace }

/**
 * A file link, tagged with the workspace it should be read from.
 *
 * The workspace travels in the address because it is not a secret and the URL
 * is worth being able to copy. The session does not: see `openFile`.
 */
function withWorkspace(url: string): string {
  const workspace = currentWorkspace()
  if (!workspace) return url
  return `${url}${url.includes('?') ? '&' : '?'}${WORKSPACE_PARAM}=${encodeURIComponent(workspace)}`
}

/**
 * Open or download one of the API's files, with the session in a header.
 *
 * An `<a href>` cannot send an Authorization header, and putting the token in
 * the query string instead would write a live session into the API's access
 * log, the browser's history and the Referer of anything the print view loads.
 * So the file is fetched here and handed to the browser as a blob: same two
 * clicks for the reader, no credential left lying in an address.
 *
 * `download` is the filename to save as; without it the file opens in a tab,
 * which is what the print view wants.
 */
export async function openFile(
  url: string,
  { download = '', signal }: { download?: string; signal?: AbortSignal } = {},
): Promise<void> {
  const response = await fetch(url, {
    headers: {
      ...(currentWorkspace() ? { [WORKSPACE_HEADER]: currentWorkspace() } : {}),
      ...(accessToken() ? { Authorization: `Bearer ${accessToken()}` } : {}),
    },
    cache: 'no-store',
    signal,
  })
  if (!response.ok) throw await httpError(response)

  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)

  if (download) {
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = download
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  } else {
    window.open(objectUrl, '_blank', 'noopener')
  }

  // Long enough for a print dialog to have loaded it, short enough that a
  // session's worth of documents is not still in memory an hour later.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 120_000)
}

/** Reads are quick or they are broken. Generation is not — see createProposal. */
const READ_TIMEOUT_MS = 15_000

const FILE_KINDS = new Set(['proposal', 'requirements'])
const FILE_EXTENSIONS = new Set(['md', 'html', 'pdf'])

/** Which of the three a file link can ask for. `FILE_EXTENSIONS` is the same set. */
type FileExtension = 'md' | 'html' | 'pdf'

/** The multipart fields POST /api/proposals accepts, in contract order. */
const FORM_FIELDS = [
  'brief',
  'currency',
  'client_name',
  'project_name',
  'market_region',
  'budget_hint',
  'timeline_hint',
]

/**
 * Used only when the server sends a status with no `detail` of its own. The
 * server's own message always wins — it knows more than this table does.
 */
const STATUS_HINTS: Record<number, string> = {
  401: 'That session is no longer valid. Sign in again.',
  404: 'That is not on this server. It was either deleted or prepared against a different generated/ directory.',
  413: 'Those files are too large. Send at most 8 images, each under 8 MB.',
  415: 'One of those files is not an image. Attach screenshots, sketches or photos only.',
  422: 'The API rejected the form. Check that the brief is filled in and the currency is a three-letter code.',
  429: 'The Gemini quota for this key is used up. Wait for it to reset, or use a different key in backend/.env.',
  500: 'The API hit an unhandled error while preparing the quotation. Check the uvicorn console for the traceback.',
  502: 'The dev proxy could not reach the API. Start it from backend/ with: py -m uvicorn app.main:app --port 8000',
  503: 'The API is running but not configured. Add GEMINI_API_KEY to backend/.env and restart it.',
  504: 'The API took too long to answer. A long brief with several images can outlast the gateway — try again with a shorter brief.',
}

/** Which way a call failed. What a caller branches on. */
export type ApiErrorKind = 'network' | 'http' | 'parse' | 'timeout' | 'aborted' | 'validation'

type ApiErrorOptions = {
  status?: number
  kind?: ApiErrorKind
  detail?: string
  cause?: unknown
}

/**
 * A failed call to the PRISM API.
 */
export class ApiError extends Error {
  /** HTTP status, or 0 when the request never landed */
  status: number
  kind: ApiErrorKind
  /** the server's own `detail`, verbatim, when it sent one */
  detail: string

  constructor(
    message: string,
    { status = 0, kind = 'http', detail = '', cause }: ApiErrorOptions = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.kind = kind
    this.detail = detail
    if (cause !== undefined) this.cause = cause
  }

  /** The API could not be reached at all — it is probably not running. */
  get isNetwork(): boolean {
    return this.kind === 'network'
  }

  /** The caller aborted deliberately; usually not worth showing. */
  get isAborted(): boolean {
    return this.kind === 'aborted'
  }
}

/**
 * Combine a caller's AbortSignal with an optional timeout, without depending on
 * `AbortSignal.any`.
 *
 * @param timeoutMs 0 disables the timeout
 */
function withTimeout(signal: AbortSignal | undefined | null, timeoutMs: number) {
  const controller = new AbortController()
  let timedOut = false

  const timer: ReturnType<typeof setTimeout> | null = timeoutMs
    ? setTimeout(() => {
        timedOut = true
        controller.abort()
      }, timeoutMs)
    : null

  const forward = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', forward, { once: true })
  }

  return {
    signal: controller.signal,
    didTimeOut: () => timedOut,
    release() {
      if (timer) clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', forward)
    },
  }
}

/** An error body, as far as this file reads it. Both fields stay unchecked. */
type ErrorBody = {
  detail?: unknown
  message?: unknown
}

/** One entry of FastAPI's request-validation `detail` array. */
type ValidationEntry = string | { loc?: unknown; msg?: unknown } | null

/**
 * Pull the most useful message out of an error response body.
 * FastAPI sends `{"detail": "..."}`, and `{"detail": [{loc, msg, type}, ...]}`
 * for request-validation failures.
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
    // `JSON.parse` hands back `any`. Naming the two fields this reads keeps them
    // `unknown` until the checks below say what they are.
    const data = JSON.parse(body) as ErrorBody | null
    const detail = data?.detail

    if (typeof detail === 'string' && detail.trim()) return detail.trim()

    if (Array.isArray(detail)) {
      const messages = detail
        .map((entry: ValidationEntry) => {
          if (typeof entry === 'string') return entry
          const field = Array.isArray(entry?.loc)
            ? entry.loc.filter((part: unknown) => part !== 'body').join('.')
            : ''
          const message = typeof entry?.msg === 'string' ? entry.msg : ''
          if (field && message) return `${field}: ${message}`
          return message || field
        })
        .filter(Boolean)
      if (messages.length) return messages.join('; ')
    }

    if (typeof data?.message === 'string' && data.message.trim()) {
      return data.message.trim()
    }
  } catch {
    // Not JSON. Fall through.
  }

  // A short plain-text body (a proxy error, say) is still informative. An HTML
  // error page is not — that is a wall of markup, not a message.
  const text = body.trim()
  if (text && text.length <= 300 && !text.startsWith('<')) return text
  return ''
}

async function httpError(response: Response): Promise<ApiError> {
  const detail = await extractDetail(response)
  const message =
    detail ||
    STATUS_HINTS[response.status] ||
    `The API answered ${response.status}${response.statusText ? ` ${response.statusText}` : ''}.`

  return new ApiError(message, {
    status: response.status,
    kind: 'http',
    detail,
  })
}

type RequestOptions = {
  method?: string
  body?: BodyInit
  signal?: AbortSignal
  headers?: Record<string, string>
  timeoutMs?: number
}

/**
 * Perform a request and return the parsed JSON body.
 *
 * @param path relative to /api
 */
async function request<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, headers, timeoutMs = READ_TIMEOUT_MS } = options
  const url = `${API_ROOT}${path}`
  const timeout = withTimeout(signal, timeoutMs)

  let response: Response
  try {
    response = await fetch(url, {
      method,
      body,
      signal: timeout.signal,
      // Caller headers merge in rather than being dropped — a JSON PUT needs its
      // own Content-Type, and multipart must never have one set by hand because
      // only the browser knows the boundary it generated.
      headers: {
        Accept: 'application/json',
        ...(currentWorkspace() ? { [WORKSPACE_HEADER]: currentWorkspace() } : {}),
        // Who is asking. Absent on an install with no accounts, where the
        // server answers everyone and says so.
        ...(accessToken() ? { Authorization: `Bearer ${accessToken()}` } : {}),
        ...(headers || {}),
      },
      // Do not let a stale bundle survive a regeneration.
      cache: 'no-store',
    })
  } catch (cause) {
    // What is thrown is `unknown`; its name is the only thing read off it.
    const name = cause && typeof cause === 'object' && 'name' in cause ? cause.name : undefined
    if (name === 'AbortError') {
      if (timeout.didTimeOut()) {
        throw new ApiError(
          `The API did not answer within ${Math.round(
            timeoutMs / 1000,
          )} seconds. Check that it is still running on port 8000.`,
          { kind: 'timeout', cause },
        )
      }
      throw new ApiError('Request cancelled.', { kind: 'aborted', cause })
    }
    throw new ApiError(
      'Could not reach the PRISM API. Start it from backend/ with: py -m uvicorn app.main:app --port 8000',
      { kind: 'network', cause },
    )
  } finally {
    timeout.release()
  }

  if (!response.ok) throw await httpError(response)

  let text: string
  try {
    text = await response.text()
  } catch (cause) {
    throw new ApiError('The API response was cut off before it finished. Try again.', {
      status: response.status,
      kind: 'parse',
      cause,
    })
  }

  // 204 No Content is the documented answer to a successful DELETE. There is
  // nothing to parse, and treating an empty body as malformed JSON would turn a
  // success into an error.
  if (response.status === 204 || text.trim() === '') return null as T

  try {
    // The one place a shape is claimed rather than checked: `T` is what the
    // caller asked the server for, and `assertBundle` and `assertJob` below are
    // where that claim is tested for the two answers worth testing.
    return JSON.parse(text) as T
  } catch (cause) {
    throw new ApiError(
      'The API answered with something that is not JSON. Check that /api is proxied to http://localhost:8000 and that the request reached FastAPI rather than the dev server.',
      { status: response.status, kind: 'parse', cause },
    )
  }
}

/** A form the caller already built, or the fields to build one from. */
export type ProposalFormInput = FormData | Record<string, unknown>

/**
 * Accept either a ready-made FormData or a plain object of form fields, and
 * return multipart the API will understand.
 *
 * Images are appended under the repeated key `images` (not `images[]`), which
 * is what FastAPI's `List[UploadFile]` binds to.
 */
function toFormData(input: ProposalFormInput): FormData {
  if (typeof FormData !== 'undefined' && input instanceof FormData) return input

  if (!input || typeof input !== 'object') {
    throw new TypeError('createProposal expects a FormData or a plain object of form fields.')
  }

  // Every FormData has returned by now. TypeScript cannot see that: the
  // `typeof FormData` guard in front of the `instanceof` leaves the other
  // branch open, so this is a view of what got past both checks.
  const fields = input as Record<string, unknown>

  const form = new FormData()

  for (const field of FORM_FIELDS) {
    const value = fields[field]
    if (value === undefined || value === null) continue
    form.append(field, String(value))
  }

  const images = fields.images ?? fields.files ?? []
  // A FileList is array-like rather than an array — that is what this branch is
  // for, and what the cast says.
  const list: unknown[] = Array.isArray(images)
    ? images
    : Array.from((images ?? []) as ArrayLike<unknown>)
  for (const file of list) {
    if (file instanceof Blob) form.append('images', file)
  }

  return form
}

/**
 * Submit the brief and get back the job preparing it.
 *
 * The answer is a 202 with a `JobView`, not a bundle. Pricing takes the better
 * part of a minute and three tiers take a minute and a half; the work now
 * happens behind the request, so closing the tab no longer throws away the
 * Gemini spend. Poll `fetchJob` until `state` is `done`, then `fetchBundle`
 * each id in `result_ids`.
 *
 * Everything that can be rejected outright — a bad currency, a schedule that
 * does not total 100 — is still rejected by this call, synchronously, before a
 * job exists. A 202 means the brief was accepted, not that it was priced.
 *
 * @returns the JobView from backend/app/jobs.py
 * @throws {ApiError}
 */
export async function createProposal(
  formData: ProposalFormInput,
  options: CallOptions = {},
): Promise<Job> {
  const body = toFormData(formData)

  const brief = String(body.get('brief') ?? '').trim()
  if (!brief) {
    throw new ApiError('Describe the job first. The brief is the one thing PRISM cannot infer.', {
      kind: 'validation',
    })
  }

  // No Content-Type header: the browser must set it, because only the browser
  // knows the multipart boundary it generated.
  const data = await request('/proposals', {
    method: 'POST',
    body,
    signal: options.signal,
    // Registering a job is quick, but the validation in front of it reads the
    // uploaded images. A generous ceiling, not none: this no longer holds the
    // whole generation open.
    timeoutMs: 60_000,
  })

  return assertJob(data)
}

/** What a revision asks for. One of the two is required — `reviseProposal` says so. */
export type ProposalChanges = {
  instruction?: string
  targetTotal?: string | number
}

/**
 * Revise a prepared quotation, optionally onto an exact total.
 *
 * The revision is a new bundle with its own id — the parent is never edited,
 * so a quotation that has already been sent stays exactly as it was sent.
 *
 * Currency is not a parameter and cannot be one: a revision inherits the
 * parent's currency, because re-pricing the same work in another currency
 * would be an exchange rate by another name.
 *
 * Like `createProposal`, this answers with a job rather than the finished
 * sheet — a revision is a second full Gemini pass over a whole estimate, so it
 * is slower than the first.
 *
 * @param id the quotation being revised
 * @returns the JobView preparing the new bundle
 * @throws {ApiError}
 */
export async function reviseProposal(
  id: string,
  changes: ProposalChanges = {},
  options: CallOptions = {},
): Promise<Job> {
  const proposalId = String(id ?? '').trim()
  if (!proposalId) {
    throw new ApiError('No quotation id to revise.', { kind: 'validation' })
  }

  const instruction = String(changes.instruction ?? '').trim()
  const targetTotal = String(changes.targetTotal ?? '').trim()

  if (!instruction && !targetTotal) {
    throw new ApiError(
      'Say what to change, or set a target total. A revision needs one of the two.',
      { kind: 'validation' },
    )
  }

  const body = new FormData()
  body.set('instruction', instruction)
  body.set('target_total', targetTotal)

  const data = await request(`/proposals/${encodeURIComponent(proposalId)}/revise`, {
    method: 'POST',
    body,
    signal: options.signal,
    timeoutMs: 60_000,
  })

  return assertJob(data)
}

/**
 * Everything PRISM has prepared or is preparing, newest first.
 *
 * Jobs outlive the page that started them and are mirrored to disk, so this
 * still answers after a reload — and after an API restart, where anything that
 * was mid-flight comes back marked failed rather than pretending to still run.
 *
 * @returns JobView rows
 */
export async function listJobs(
  query: { limit?: number } = {},
  options: CallOptions = {},
): Promise<Job[]> {
  const params = new URLSearchParams()
  if (query.limit) params.set('limit', String(query.limit))
  const suffix = params.toString() ? `?${params}` : ''

  const data = await request(`/jobs${suffix}`, options)
  if (!Array.isArray(data)) {
    throw new ApiError('The job list came back in a shape this page cannot read.', {
      kind: 'parse',
    })
  }
  return data.map(assertJob)
}

/**
 * One job, for polling while it runs.
 *
 * @returns the JobView
 */
export async function fetchJob(id: string, options: CallOptions = {}): Promise<Job> {
  const jobId = String(id ?? '').trim()
  if (!jobId) throw new ApiError('No job id to look up.', { kind: 'validation' })

  const data = await request(`/jobs/${encodeURIComponent(jobId)}`, options)
  return assertJob(data)
}

/**
 * Every quotation on file, newest first.
 *
 * Returns summaries, not bundles — a bundle carries both rendered documents and
 * runs to tens of kilobytes.
 */
export async function listProposals(
  query: { q?: string; limit?: number } = {},
  options: CallOptions = {},
): Promise<ProposalSummary[]> {
  const params = new URLSearchParams()
  if (query.q) params.set('q', String(query.q).trim())
  if (query.limit) params.set('limit', String(query.limit))
  const suffix = params.toString() ? `?${params}` : ''

  const data = await request<ProposalSummary[]>(`/proposals${suffix}`, options)
  if (!Array.isArray(data)) {
    throw new ApiError('The quotation list came back in a shape this page cannot read.', {
      kind: 'parse',
    })
  }
  return data
}

/**
 * Delete one quotation and both of its documents. Not recoverable.
 *
 * Revisions are independent bundles: deleting a parent leaves its revisions in
 * place.
 */
export async function deleteProposal(id: string, options: CallOptions = {}): Promise<void> {
  const proposalId = String(id ?? '').trim()
  if (!proposalId) {
    throw new ApiError('No quotation id to delete.', { kind: 'validation' })
  }
  await request(`/proposals/${encodeURIComponent(proposalId)}`, {
    method: 'DELETE',
    signal: options.signal,
  })
}

/** The values a new brief form opens with. */
export async function fetchSettings(options: CallOptions = {}): Promise<StudioDefaults> {
  return request<StudioDefaults>('/settings', options)
}

/** Save what a new brief form opens with. */
export async function saveSettings(
  defaults: StudioDefaults,
  options: CallOptions = {},
): Promise<StudioDefaults> {
  return request<StudioDefaults>('/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(defaults),
    signal: options.signal,
  })
}

/**
 * Fetch a bundle that was prepared earlier.
 *
 * @returns the ProposalBundle from backend/app/schemas.py
 * @throws {ApiError}
 */
export async function fetchBundle(id: string, options: CallOptions = {}): Promise<ProposalBundle> {
  const proposalId = String(id ?? '').trim()
  if (!proposalId) {
    throw new ApiError('No quotation id to look up.', { kind: 'validation' })
  }

  const data = await request(`/proposals/${encodeURIComponent(proposalId)}`, options)
  return assertBundle(data)
}

/**
 * A response that parsed as JSON but is not a bundle means something answered
 * in the API's place — usually the dev server returning index.html because the
 * proxy is not wired up. Say that, rather than letting the UI destructure
 * undefined three components deep.
 */
function assertBundle(data: unknown): ProposalBundle {
  const ok =
    data &&
    typeof data === 'object' &&
    'id' in data &&
    typeof data.id === 'string' &&
    'estimate' in data &&
    data.estimate &&
    typeof data.estimate === 'object' &&
    'files' in data &&
    Array.isArray(data.files)

  if (!ok) {
    throw new ApiError(
      'The API returned JSON that is not a quotation bundle. Check that /api is proxied to the FastAPI process on port 8000.',
      { kind: 'parse' },
    )
  }
  // The check above tells a bundle from an index.html; it is not a validation.
  // What each field holds is the server's schema keeping its word.
  return data as ProposalBundle
}

/**
 * The same guard for a job. A bundle and a job both have an `id`, so checking
 * for one is not enough to tell them apart — `state` and `steps` are what make
 * this a job rather than a quotation or a dev-server index.html.
 *
 * Missing arrays are filled in rather than rejected: a job with no steps is a
 * legitimate shape, and a progress bar is not worth an error page.
 */
function assertJob(data: unknown): Job {
  const ok =
    data &&
    typeof data === 'object' &&
    'id' in data &&
    typeof data.id === 'string' &&
    'state' in data &&
    typeof data.state === 'string'

  if (!ok) {
    throw new ApiError(
      'The API answered without a job in it. Check that /api is proxied to the FastAPI process on port 8000.',
      { kind: 'parse' },
    )
  }

  // Same claim as `assertBundle` makes: the guard tells a job from a bundle, the
  // server's schema is what makes the rest of the fields what they say. The
  // three checks below still run, because a written job file can be older.
  const job = data as Job

  return {
    ...job,
    steps: Array.isArray(job.steps) ? job.steps : [],
    result_ids: Array.isArray(job.result_ids) ? job.result_ids : [],
    progress: typeof job.progress === 'number' ? job.progress : 0,
  }
}

/**
 * Build a proposal from one quotation, exactly as that quotation stands.
 *
 * Answers with a job, like every other Gemini pass: the model writes the
 * argument, the server prints the figures from the quotation and the terms
 * from settings, and the finished document lands in `result_ids`.
 *
 * @returns the JobView building it
 */
export async function buildDocument(
  quotationId: string,
  options: CallOptions = {},
): Promise<Job> {
  const id = String(quotationId ?? '').trim()
  if (!id) {
    throw new ApiError('Choose a quotation to build the proposal from.', { kind: 'validation' })
  }

  const body = new FormData()
  body.set('quotation_id', id)

  const data = await request('/documents', {
    method: 'POST',
    body,
    signal: options.signal,
    timeoutMs: 60_000,
  })
  return assertJob(data)
}

/**
 * Every proposal built, newest first. Summaries, not whole documents.
 */
export async function listDocuments(
  query: { limit?: number } = {},
  options: CallOptions = {},
): Promise<ProposalDocumentSummary[]> {
  const params = new URLSearchParams()
  if (query.limit) params.set('limit', String(query.limit))
  const suffix = params.toString() ? `?${params}` : ''

  const data = await request<ProposalDocumentSummary[]>(`/documents${suffix}`, options)
  if (!Array.isArray(data)) {
    throw new ApiError('The proposal list came back in a shape this page cannot read.', {
      kind: 'parse',
    })
  }
  return data
}

/** One built proposal, with its narrative, its terms snapshot and its files. */
export async function fetchDocument(
  id: string,
  options: CallOptions = {},
): Promise<ProposalDocument> {
  const documentId = String(id ?? '').trim()
  if (!documentId) throw new ApiError('No proposal id to look up.', { kind: 'validation' })

  const data = await request<ProposalDocument>(
    `/documents/${encodeURIComponent(documentId)}`,
    options,
  )
  if (!data || typeof data !== 'object' || typeof data.id !== 'string') {
    throw new ApiError('That answer was not a proposal.', { kind: 'parse' })
  }
  return data
}

/** Delete one proposal. The quotation it was built from is untouched. */
export async function deleteDocument(id: string, options: CallOptions = {}): Promise<void> {
  const documentId = String(id ?? '').trim()
  if (!documentId) throw new ApiError('No proposal id to delete.', { kind: 'validation' })
  await request(`/documents/${encodeURIComponent(documentId)}`, {
    method: 'DELETE',
    signal: options.signal,
  })
}

/**
 * URL of a built proposal's document.
 *
 *   documentUrl('a1b2', 'pdf') -> '/api/documents/a1b2/files/proposal.pdf'
 */
export function documentUrl(id: string, ext: FileExtension = 'md'): string {
  const documentId = String(id ?? '').trim()
  if (!documentId) throw new TypeError('documentUrl needs a proposal id.')

  const extension = String(ext ?? 'md')
    .trim()
    .toLowerCase()
    .replace(/^\./, '')
  if (!FILE_EXTENSIONS.has(extension)) {
    throw new TypeError(
      `documentUrl ext must be one of ${[...FILE_EXTENSIONS].join(', ')}, received '${ext}'.`,
    )
  }
  return withWorkspace(
    `${API_ROOT}/documents/${encodeURIComponent(documentId)}/files/proposal.${extension}`,
  )
}

/**
 * The currencies the server will price in.
 *
 * This never rejects. The built-in `CURRENCIES` list is a complete, valid
 * answer, so a currency select has no reason to break because the API is not up
 * yet — it just shows the fallback until the server list arrives.
 */
export async function fetchCurrencies(
  options: CallOptions = {},
): Promise<ReadonlyArray<Currency>> {
  try {
    const data = await request<Currency[]>('/currencies', options)

    if (!Array.isArray(data)) throw new Error('not an array')

    const list = data
      .filter((entry) => entry && typeof entry.code === 'string' && entry.code.trim())
      .map((entry) => ({
        code: entry.code.trim().toUpperCase(),
        name: typeof entry.name === 'string' ? entry.name : entry.code,
        symbol: typeof entry.symbol === 'string' ? entry.symbol : '',
      }))

    return list.length ? list : CURRENCIES
  } catch (error) {
    if (error instanceof ApiError && error.isAborted) throw error
    console.warn('PRISM: /api/currencies is unavailable, using the built-in currency list.', error)
    return CURRENCIES
  }
}

/**
 * URL of one of a bundle's generated documents.
 *
 *   fileUrl('a1b2', 'proposal', 'md')       -> '/api/proposals/a1b2/files/proposal.md'
 *   fileUrl('a1b2', 'requirements', 'html') -> '/api/proposals/a1b2/files/requirements.html'
 *
 * `.md` downloads as an attachment; `.html` opens a printable page — that is
 * the print-to-PDF path, and the reason there is no PDF library in this
 * project.
 *
 * Each `GeneratedFile` in a bundle already carries `download_url` and
 * `print_url`; prefer those and use this when you only have an id.
 */
export function fileUrl(
  id: string,
  kind: DocumentKind = 'proposal',
  ext: FileExtension = 'md',
): string {
  const proposalId = String(id ?? '').trim()
  if (!proposalId) throw new TypeError('fileUrl needs a proposal id.')

  const documentKind = String(kind ?? '')
    .trim()
    .toLowerCase()
  if (!FILE_KINDS.has(documentKind)) {
    throw new TypeError(`fileUrl kind must be 'proposal' or 'requirements', received '${kind}'.`)
  }

  const extension = String(ext ?? 'md')
    .trim()
    .toLowerCase()
    .replace(/^\./, '')
  if (!FILE_EXTENSIONS.has(extension)) {
    throw new TypeError(
      `fileUrl ext must be one of ${[...FILE_EXTENSIONS].join(', ')}, received '${ext}'.`,
    )
  }

  return withWorkspace(
    `${API_ROOT}/proposals/${encodeURIComponent(
      proposalId,
    )}/files/${documentKind}.${extension}`,
  )
}

/**
 * Every workspace on this install, oldest first.
 *
 * Each carries its own counts and its own studio name, which is what makes the
 * switcher readable: "Neptune Labs - 159 quotations" says more than a slug.
 */
export async function listWorkspaces(options: CallOptions = {}): Promise<Workspace[]> {
  const data = await request<Workspace[]>('/workspaces', options)
  if (!Array.isArray(data)) {
    throw new ApiError('The workspace list came back in a shape this page cannot read.', {
      kind: 'parse',
    })
  }
  return data
}

export async function createWorkspace(
  name: string,
  options: CallOptions = {},
): Promise<Workspace> {
  const trimmed = String(name ?? '').trim()
  if (!trimmed) throw new ApiError('A workspace needs a name.', { kind: 'validation' })
  return request<Workspace>('/workspaces', {
    method: 'POST',
    body: JSON.stringify({ name: trimmed }),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
}

export async function renameWorkspace(
  id: string,
  name: string,
  options: CallOptions = {},
): Promise<Workspace> {
  const workspaceId = String(id ?? '').trim()
  const trimmed = String(name ?? '').trim()
  if (!workspaceId) throw new ApiError('No workspace to rename.', { kind: 'validation' })
  if (!trimmed) throw new ApiError('A workspace needs a name.', { kind: 'validation' })
  return request<Workspace>(`/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name: trimmed }),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
}

export async function deleteWorkspace(id: string, options: CallOptions = {}): Promise<void> {
  const workspaceId = String(id ?? '').trim()
  if (!workspaceId) throw new ApiError('No workspace to delete.', { kind: 'validation' })
  await request(`/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: 'DELETE',
    signal: options.signal,
  })
}

/**
 * Who is on the workspace you have open, and what you may do in it.
 *
 * `your_role` is the server's answer, not the client's guess — the screens use
 * it to hide what would be refused anyway, and the refusal is still the real
 * boundary.
 */
export async function fetchTeam(options: CallOptions = {}): Promise<Team> {
  return request<Team>('/team', options)
}

export async function inviteToTeam(
  email: string,
  role: MemberRole = 'member',
  options: CallOptions = {},
): Promise<Invite> {
  const address = String(email ?? '').trim()
  if (!address) throw new ApiError('An invitation needs an email address.', { kind: 'validation' })
  return request<Invite>('/team/invites', {
    method: 'POST',
    body: JSON.stringify({ email: address, role }),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
}

export async function revokeInvite(token: string, options: CallOptions = {}): Promise<void> {
  await request(`/team/invites/${encodeURIComponent(token)}`, {
    method: 'DELETE',
    signal: options.signal,
  })
}

export async function setMemberRole(
  email: string,
  role: MemberRole,
  options: CallOptions = {},
): Promise<Member> {
  return request<Member>(`/team/members/${encodeURIComponent(email)}`, {
    method: 'PATCH',
    body: JSON.stringify({ role }),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
}

export async function removeMember(email: string, options: CallOptions = {}): Promise<void> {
  await request(`/team/members/${encodeURIComponent(email)}`, {
    method: 'DELETE',
    signal: options.signal,
  })
}

/** What an invitation link is for. Readable before signing in — the token is the secret. */
export async function fetchInvite(
  token: string,
  options: CallOptions = {},
): Promise<InvitePreview> {
  return request<InvitePreview>(`/invites/${encodeURIComponent(token)}`, options)
}

export async function acceptInvite(token: string, options: CallOptions = {}): Promise<Team> {
  return request<Team>(`/invites/${encodeURIComponent(token)}/accept`, {
    method: 'POST',
    signal: options.signal,
  })
}

/**
 * Your own mail in the workspace the header names.
 *
 * Goes through `request()` like everything else: a raw fetch would drop the
 * workspace header (the server would answer from the first workspace — wrong
 * data, no error) and the bearer token.
 */
export async function listNotifications(
  { limit = 30 }: { limit?: number } = {},
  options: CallOptions = {},
): Promise<Mailbox> {
  return request<Mailbox>(`/notifications?limit=${encodeURIComponent(limit)}`, options)
}

export async function markNotificationsRead(
  { through = '', ids = [] }: { through?: string; ids?: string[] } = {},
  options: CallOptions = {},
): Promise<Mailbox> {
  return request<Mailbox>('/notifications/read', {
    method: 'POST',
    body: JSON.stringify({ through, ids }),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
}

export async function clearNotifications(options: CallOptions = {}): Promise<Mailbox> {
  return request<Mailbox>('/notifications', { method: 'DELETE', signal: options.signal })
}

/** Take charge of a workspace nobody administers yet. Fails if it has a team. */
export async function claimWorkspace(options: CallOptions = {}): Promise<Team> {
  return request<Team>('/team/claim', { method: 'POST', signal: options.signal })
}

/**
 * One `revisions` entry as it actually arrives, before `readRevisions` has
 * decided whether it is one. Every field is `unknown` because that is the
 * whole point - the same shape `CurrencyLike` uses in `CurrencySelect`, for
 * the same reason.
 */
type RevisionLike = { asked?: unknown; at?: unknown }

/**
 * `Intake.revisions`, narrowed to the two keys this client knows about.
 *
 * The server's field is `List[dict]` with no model behind it, so what lands
 * here is whatever was written to disk - and the screens that read it are
 * going to render a client's own words into the page. Coerced rather than
 * trusted, exactly as `App.tsx` coerces `intake.scope`: a third key somebody
 * adds upstream is dropped, and an entry missing one of the two renders as an
 * empty string rather than as `undefined`.
 */
function readRevisions(value: unknown): IntakeRevision[] {
  if (!Array.isArray(value)) return []
  return value.map((entry: RevisionLike) => ({
    asked: String((entry && entry.asked) || ''),
    at: String((entry && entry.at) || ''),
  }))
}

/**
 * The one place an `Intake` crosses into this client from the wire.
 *
 * Applied to every intake every call in this module hands back, so no screen
 * has to ask whether the one it happens to be holding went through the
 * coercion above. Nothing else on the record is rewritten: the rest of it is
 * plain strings and a string array, which `response_model` does guarantee.
 */
function readIntake<T extends Intake>(entry: T): T {
  return { ...entry, revisions: readRevisions(entry.revisions) }
}

/**
 * The two calls that mint a link, checked before their answer is handed on.
 *
 * `link` is the single string in this feature with no second chance: it is
 * shown once and no route will ever return it again, so an answer missing it
 * would put `value={undefined}` in the field and hand `writeText(undefined)`
 * to the clipboard - a studio staring at an empty box with nothing to copy and
 * no idea why. `response_model=IntakeIssued` makes that unlikely rather than
 * impossible, and unlikely is the wrong bar for the one value that cannot be
 * refetched.
 *
 * The message says the request **was** recorded, deliberately. It was: the
 * server created it before serialising this answer, and a message implying
 * otherwise gets a studio pressing Generate a second time and ending up with
 * two intakes for one client.
 */
function readIssued(issued: IntakeIssued, what: string): IntakeIssued {
  if (!issued || typeof issued.id !== 'string' || typeof issued.link !== 'string' || !issued.link) {
    throw new ApiError(
      `The request was ${what}, but its link did not come back. Open the queue to find it, then reissue the link.`,
      { kind: 'parse' },
    )
  }
  return readIntake(issued)
}

/**
 * The client-request queue, newest first. Readable by any member; generating
 * one, sending it, reissuing its link and closing it are each an admin's call -
 * see the four below.
 */
export async function listIntakes(options: CallOptions = {}): Promise<Intake[]> {
  const data = await request<Intake[]>('/intakes', options)
  if (!Array.isArray(data)) {
    throw new ApiError('The request queue came back in a shape this page cannot read.', {
      kind: 'parse',
    })
  }
  return data.map(readIntake)
}

/** One client request. 404s when the id is absent or malformed. */
export async function fetchIntake(id: string, options: CallOptions = {}): Promise<Intake> {
  const intakeId = String(id ?? '').trim()
  if (!intakeId) throw new ApiError('No request id to look up.', { kind: 'validation' })

  const data = await request<Intake>(`/intakes/${encodeURIComponent(intakeId)}`, options)
  if (!data || typeof data !== 'object' || typeof data.id !== 'string') {
    throw new ApiError('That answer was not a client request.', { kind: 'parse' })
  }
  return readIntake(data)
}

/**
 * Generate a client request from the studio's own PAD configuration, and get
 * back the link that request lives behind. Admin-only - issuing one is nearer
 * to inviting somebody than to drafting a quotation, per `main.create_intake`.
 *
 * `IntakeIssued`, not `Intake`: the response carries a `link` and this is one
 * of only two calls that ever does. `relinkIntake` is the other, and there is
 * no third - so a caller that drops this return value has thrown away the only
 * copy of that link there will ever be.
 *
 * The body is the preset alone. The client's own email, phone, scope and
 * budget used to be typed here by the studio; from Stage 2 they arrive through
 * the client's own link (`POST /api/client/{token}/submit`) and `IntakeRequest`
 * no longer has fields for them. `preset` stays a loose record to mirror the
 * server's own loose `dict` - `IntakePreset` in `types.ts` is what the studio's
 * screens agree to put in it.
 */
export async function createIntake(
  body: { preset: Record<string, unknown> },
  options: CallOptions = {},
): Promise<IntakeIssued> {
  const issued = await request<IntakeIssued>('/intakes', {
    method: 'POST',
    body: JSON.stringify({ preset: body.preset || {} }),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
  return readIssued(issued, 'recorded')
}

/**
 * Hand a prepared quotation to the client: `quoted -> sent`. Admin-only, and
 * `bundleId` is required rather than inferred - the server refuses a bundle
 * that is not this request's own, and a re-quoted request can have more than
 * one candidate on file, so which one the client sees is a decision somebody
 * makes rather than an accident of ordering.
 */
export async function sendIntake(
  id: string,
  bundleId: string,
  options: CallOptions = {},
): Promise<Intake> {
  const intakeId = String(id ?? '').trim()
  if (!intakeId) throw new ApiError('No request id to send.', { kind: 'validation' })
  const bundle = String(bundleId ?? '').trim()
  if (!bundle) throw new ApiError('Say which quotation to send.', { kind: 'validation' })

  const sent = await request<Intake>(`/intakes/${encodeURIComponent(intakeId)}/send`, {
    method: 'POST',
    body: JSON.stringify({ bundle_id: bundle }),
    headers: { 'Content-Type': 'application/json' },
    signal: options.signal,
  })
  return readIntake(sent)
}

/**
 * Reissue a client's link, killing the one before it outright. Admin-only.
 *
 * Destructive in a way the name does not say on its own: the link a client is
 * holding - possibly the one they are reading their quotation through right
 * now - stops working the moment this returns. It is also the only way back to
 * a link that was never copied, which is why the answer is `IntakeIssued` and
 * why a caller has to show what it gets.
 */
export async function relinkIntake(id: string, options: CallOptions = {}): Promise<IntakeIssued> {
  const intakeId = String(id ?? '').trim()
  if (!intakeId) throw new ApiError('No request id to reissue.', { kind: 'validation' })

  const issued = await request<IntakeIssued>(`/intakes/${encodeURIComponent(intakeId)}/relink`, {
    method: 'POST',
    signal: options.signal,
  })
  return readIssued(issued, 'reissued')
}

/** Not going ahead. Admin-only; 404s when the id is absent or malformed. */
export async function closeIntake(id: string, options: CallOptions = {}): Promise<Intake> {
  const intakeId = String(id ?? '').trim()
  if (!intakeId) throw new ApiError('No request id to close.', { kind: 'validation' })

  const closed = await request<Intake>(`/intakes/${encodeURIComponent(intakeId)}/close`, {
    method: 'POST',
    signal: options.signal,
  })
  return readIntake(closed)
}
