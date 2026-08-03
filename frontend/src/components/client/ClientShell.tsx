import { useEffect, useState } from 'react'
import { ClientApiError, fetchClientIntake } from '../../lib/clientApi'
import { formatDate } from '../../lib/format'
import { DISPLAY, MONO_LABEL } from '../tokens'
import type {
  ClientIntakeView,
  ClientIssuedView,
  ClientQuotationView,
  ClientWaitingView,
} from '../../types'

/**
 * What `main.tsx` renders instead of `<AuthGate><App/></AuthGate>` when the
 * hash names a client link (`#/c/<token>`) - see that file's own comment on
 * why this has to happen ahead of the gate rather than as an allowlist
 * inside it. Nothing below this component, directly or by way of what it
 * imports, may reach `lib/api.ts`, `lib/auth.ts` or `lib/workspace.ts`: a
 * client holding this link has no studio session, and the whole reason
 * `lib/clientApi.ts` exists as its own module is so that fact cannot be
 * undone by an import.
 *
 * This component owns exactly one thing: turning a token into a
 * `ClientIntakeView` and picking which face of the client experience shows
 * it. The four faces themselves - the intake form, the waiting notice, the
 * quotation, and the closed/gone notice - are Task 8's; what stands in for
 * each here is a placeholder that renders enough of the real data to prove
 * the plumbing works, deliberately not a design.
 */

type ClientShellProps = { token: string }

/** `'ready'` pairs with a `ClientIntakeView`; the other three do not. */
type ShellStatus = 'loading' | 'ready' | 'gone' | 'offline'

export default function ClientShell({ token }: ClientShellProps) {
  const [status, setStatus] = useState<ShellStatus>('loading')
  const [view, setView] = useState<ClientIntakeView | null>(null)
  const [offlineMessage, setOfflineMessage] = useState('')

  useEffect(() => {
    let live = true
    setStatus('loading')
    setView(null)

    fetchClientIntake(token)
      .then((found) => {
        if (!live) return
        setView(found)
        setStatus('ready')
      })
      .catch((failure: unknown) => {
        if (!live) return
        // Every refusal this door gives - unknown token, expired, closed,
        // wrong state - is the identical opaque 404 (`ClientApiError`'s own
        // `isGone`/`kind === 'http'`), and Task 8's own plan is explicit
        // that all of those read as one page: "the identical page for
        // closed, expired, wrong and never-existed." A network failure,
        // a timeout, or an answer that did not parse is a different claim -
        // not "this link is not valid," but "this page could not ask the
        // question" - so it gets its own status rather than being folded
        // into the same bucket, where it would tell someone their link was
        // dead when the API was simply unreachable.
        if (failure instanceof ClientApiError && failure.kind === 'http') {
          setStatus('gone')
          return
        }
        setOfflineMessage(
          failure instanceof Error && failure.message
            ? failure.message
            : 'That could not be read.',
        )
        setStatus('offline')
      })

    return () => {
      live = false
    }
  }, [token])

  return (
    <div className="flex h-dvh items-center justify-center bg-canvas px-4 py-10 font-body text-body">
      {/* `max-h-dvh` + its own scroll, the same idiom `AuthScreen.tsx` uses
          inside `AuthGate`'s identical `h-dvh` wrapper: the outer frame stays
          fixed to the viewport and this card scrolls itself once a face -
          the quotation placeholder, say - runs taller than the screen. */}
      <div className="max-h-dvh w-full max-w-[32rem] overflow-y-auto">
        {status === 'loading' ? <LoadingFace /> : null}
        {status === 'offline' ? <OfflineFace message={offlineMessage} /> : null}
        {status === 'gone' ? <ClosedFace /> : null}
        {status === 'ready' && view ? <ReadyFace view={view} /> : null}
      </div>
    </div>
  )
}

/** Picks a face by `view.state`. A plain if-chain, not a lookup table: each
 * branch narrows `view` to one member of the union, which a table keyed on
 * the same strings could not do without a cast. */
function ReadyFace({ view }: { view: ClientIntakeView }) {
  if (view.state === 'issued') return <IssuedFace view={view} />
  if (view.state === 'waiting') return <WaitingFace view={view} />
  if (view.state === 'closed') return <ClosedFace />
  // The only member left is `ClientQuotationView` - `'sent'`,
  // `'revision_requested'` or `'finalized'`.
  return <QuotationFace view={view} />
}

function LoadingFace() {
  return (
    <div aria-busy="true" className="px-6 py-16 text-center">
      <p className={MONO_LABEL}>Reading your link</p>
    </div>
  )
}

function OfflineFace({ message }: { message: string }) {
  return (
    <div className="rounded-[18px] border border-rule bg-paper p-7 shadow-raised">
      <p className={MONO_LABEL}>Could not load</p>
      <h1 className={`${DISPLAY} mt-3 text-[22px] leading-[1.2] text-ink`}>
        This page could not reach the studio
      </h1>
      <p className="mt-3 max-w-[46ch] text-[14.5px] leading-[1.6] text-void">
        {message || 'Something interrupted the request.'} Your link itself has not been checked
        yet - reload to try again.
      </p>
    </div>
  )
}

/**
 * Placeholder for `ClientForm.tsx` (Task 8). `issued`: the link is live and
 * nothing has been submitted yet.
 */
function IssuedFace({ view }: { view: ClientIssuedView }) {
  return (
    <div className="rounded-[18px] border border-rule bg-paper p-7 shadow-raised">
      <p className={MONO_LABEL}>{view.studio_name || 'This studio'}</p>
      <h1 className={`${DISPLAY} mt-3 text-[22px] leading-[1.2] text-ink`}>
        Tell {view.studio_name || 'the studio'} about the work
      </h1>
      <p className="mt-3 max-w-[46ch] text-[14.5px] leading-[1.6] text-void">
        Placeholder for the intake form - Task 8. State on file:{' '}
        <code className="rounded bg-duplicate px-1.5 py-0.5 font-label text-[13px] text-ink">
          issued
        </code>
        .
      </p>
    </div>
  )
}

/**
 * Placeholder for `ClientWaiting.tsx` (Task 8). One face for `submitted`,
 * `preparing`, `quoted` and `quote_failed` - the server has already
 * collapsed all four into this single `'waiting'` shape, and nothing here
 * may try to tell them apart again.
 */
function WaitingFace({ view }: { view: ClientWaitingView }) {
  return (
    <div className="rounded-[18px] border border-rule bg-paper p-7 shadow-raised">
      <p className={MONO_LABEL}>{view.studio_name || 'This studio'}</p>
      <h1 className={`${DISPLAY} mt-3 text-[22px] leading-[1.2] text-ink`}>
        {view.studio_name || 'The studio'} has your scope
      </h1>
      <p className="mt-3 max-w-[46ch] text-[14.5px] leading-[1.6] text-void">
        Nobody has replied yet. Placeholder for the waiting face - Task 8.
      </p>
      <dl className="mt-5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-label text-[13px] text-void">
        {/* `sent_at` is `intake.created_at` - see `ClientWaitingView`'s own
            comment in types.ts on why this is the link's own issue date, not
            the client's submission time, and why the label below says so
            rather than "Submitted". */}
        <dt className="text-faint">Link issued</dt>
        <dd className="text-ink">{view.sent_at ? formatDate(view.sent_at) : '—'}</dd>
        <dt className="text-faint">From</dt>
        <dd className="text-ink">{view.email || '—'}</dd>
        <dt className="text-faint">Scope</dt>
        <dd className="text-ink">{view.scope_length} characters</dd>
      </dl>
    </div>
  )
}

/**
 * Placeholder for `ClientQuotation.tsx` (Task 8). `sent`, `revision_requested`
 * and `finalized` all land here - one shape, `state` is the only thing that
 * tells them apart.
 */
function QuotationFace({ view }: { view: ClientQuotationView }) {
  return (
    <div className="rounded-[18px] border border-rule bg-paper p-7 shadow-raised">
      <p className={MONO_LABEL}>{view.studio_name || 'This studio'}</p>
      <h1 className={`${DISPLAY} mt-3 text-[22px] leading-[1.2] text-ink`}>{view.reference}</h1>
      <p className="mt-3 max-w-[46ch] text-[14.5px] leading-[1.6] text-void">
        Placeholder for the quotation face - Task 8. State on file:{' '}
        <code className="rounded bg-duplicate px-1.5 py-0.5 font-label text-[13px] text-ink">
          {view.state}
        </code>
        .
      </p>
      <dl className="mt-5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-label text-[13px] text-void">
        <dt className="text-faint">Total</dt>
        <dd className="text-ink">
          {view.total} {view.currency}
        </dd>
        <dt className="text-faint">Valid for</dt>
        <dd className="text-ink">{view.validity} days</dd>
        <dt className="text-faint">Sent</dt>
        <dd className="text-ink">{view.sent_at ? formatDate(view.sent_at) : '—'}</dd>
        <dt className="text-faint">Revisions asked</dt>
        <dd className="text-ink">{view.revisions.length}</dd>
        <dt className="text-faint">Can revise / finalize</dt>
        <dd className="text-ink">
          {view.can_revise ? 'yes' : 'no'} / {view.can_finalize ? 'yes' : 'no'}
        </dd>
      </dl>
    </div>
  )
}

/**
 * Placeholder for `ClientClosed.tsx` (Task 8) - "the identical page for
 * closed, expired, wrong and never-existed." Reached two ways: a real
 * `{state: 'closed'}` (which `clientview.of` can build but this route never
 * actually sends - see `ClientClosedView`'s own comment) and the `'gone'`
 * shell status above, which is every 404 this door answers with. Neither
 * carries anything worth showing beyond the fact itself, so this takes no
 * props at all - one render for both paths, on purpose.
 */
function ClosedFace() {
  return (
    <div className="rounded-[18px] border border-rule bg-paper p-7 shadow-raised">
      <p className={MONO_LABEL}>Not available</p>
      <h1 className={`${DISPLAY} mt-3 text-[22px] leading-[1.2] text-ink`}>
        This link is not open right now
      </h1>
      <p className="mt-3 max-w-[46ch] text-[14.5px] leading-[1.6] text-void">
        Placeholder for the closed face - Task 8.
      </p>
    </div>
  )
}
