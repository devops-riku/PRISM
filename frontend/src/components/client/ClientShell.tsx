import { useEffect, useState } from 'react'
import { ClientApiError, fetchClientIntake } from '../../lib/clientApi'
import { DISPLAY, MONO_LABEL } from '../tokens'
import ClientClosed from './ClientClosed'
import ClientForm from './ClientForm'
import ClientQuotation from './ClientQuotation'
import ClientWaiting from './ClientWaiting'
import type { ClientIntakeView } from '../../types'

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
 * This component owns turning a token into a `ClientIntakeView`, picking
 * which of Task 8's four faces shows it, and threading state forward when
 * one of them writes: `ClientForm`'s submit, and `ClientQuotation`'s revise
 * and finalize, each hand back the fresh `ClientIntakeView` their own write
 * route answered with, and `ReadyFace` re-renders off it directly - no
 * separate refetch, and no local notion of "what state are we in" that
 * could drift from what the server actually recorded.
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
        // Every refusal `read_client_view` itself gives - unknown token,
        // expired, closed, wrong state - is the identical opaque 404
        // (`ClientApiError.isGone`), and Task 8's own plan is explicit that
        // all of those read as one page: "the identical page for closed,
        // expired, wrong and never-existed." That is a property of *that
        // one handler* today, not something this client is entitled to
        // assume of every HTTP status - branching on `kind === 'http'`
        // instead of `isGone` would route a 500 from a future code path, or
        // a 502/503 from any proxy sitting in front of the API, into this
        // same "your link is dead" bucket. A stranger reading that has no
        // way to tell a transient outage from an actually-revoked link, so
        // they stop trying - which is a worse failure than the shell simply
        // saying it could not ask the question right now.
        if (failure instanceof ClientApiError && failure.isGone) {
          setStatus('gone')
          return
        }
        if (failure instanceof ClientApiError && failure.kind === 'http') {
          // Some other HTTP status - not this door's own "no." Deliberately
          // *not* `failure.message`: for an HTTP failure that message is
          // built from the server's own `detail`
          // (`clientApi.ts`'s `extractDetail`), which for a 404 is the
          // fixed, public `_CLIENT_LINK_GONE` sentence but for anything
          // else could be a raw exception message or a proxy's own error
          // page - never something to print verbatim onto a stranger's
          // screen.
          setOfflineMessage('The studio’s system is having trouble right now. Try this link again shortly.')
          setStatus('offline')
          return
        }
        // Network, timeout, parse or validation failures. `clientApi.ts`
        // only ever puts a client-authored, already-generic sentence on
        // these - never server text - so showing `failure.message` here
        // carries none of the risk the branch above guards against.
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

  // Handed to `ClientForm` and `ClientQuotation`, whose write routes each
  // answer with a fresh `ClientIntakeView` of the same intake they just
  // moved (`/submit`, `/revise` and `/finalize` all end by calling
  // `clientview.of` on the record they wrote). Swapping it straight in is
  // what lets, say, a successful `/submit` move the page from the form to
  // the waiting face without a second round trip back through
  // `fetchClientIntake`.
  const handleUpdate = (next: ClientIntakeView) => setView(next)

  // A write can discover the link is gone just as easily as the initial
  // read can - the token can expire, or the studio can close the intake,
  // between this page loading and a client pressing Finalize. Routed to the
  // same `gone` status the read path uses, so a write's own 404 lands on
  // the identical closed face rather than a face-local error string that
  // would have to reinvent "this link no longer works."
  const handleGone = () => {
    setView(null)
    setStatus('gone')
  }

  // The quotation face is a full document - a rendered proposal, a payment
  // schedule, a revision history - and does not fit the compact, vertically
  // centred card every other face uses. It gets its own page-scrolling
  // frame at a document-friendly width; every other status keeps the
  // original fixed-viewport card, unchanged.
  const isDocument = status === 'ready' && view !== null && view.state !== 'issued' && view.state !== 'waiting'

  if (isDocument && view) {
    return (
      <div className="min-h-dvh bg-canvas px-4 py-10 font-body text-body sm:px-6 sm:py-14">
        <div className="mx-auto w-full max-w-[46rem]">
          <ReadyFace view={view} token={token} onUpdate={handleUpdate} onGone={handleGone} />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-dvh items-center justify-center bg-canvas px-4 py-10 font-body text-body">
      {/* `max-h-dvh` + its own scroll, the same idiom `AuthScreen.tsx` uses
          inside `AuthGate`'s identical `h-dvh` wrapper: the outer frame stays
          fixed to the viewport and this card scrolls itself once a face -
          a long form on a short phone screen, say - runs taller than the
          screen. */}
      <div className="max-h-dvh w-full max-w-[32rem] overflow-y-auto">
        {status === 'loading' ? <LoadingFace /> : null}
        {status === 'offline' ? <OfflineFace message={offlineMessage} /> : null}
        {status === 'gone' ? <ClientClosed /> : null}
        {status === 'ready' && view ? (
          <ReadyFace view={view} token={token} onUpdate={handleUpdate} onGone={handleGone} />
        ) : null}
      </div>
    </div>
  )
}

/** Picks a face by `view.state`. A plain if-chain, not a lookup table: each
 * branch narrows `view` to one member of the union, which a table keyed on
 * the same strings could not do without a cast. `token`, `onUpdate` and
 * `onGone` are only ever read by the two faces that write - `ClientWaiting`
 * and `ClientClosed` take no props at all, by design (see their own files).
 */
function ReadyFace({
  view,
  token,
  onUpdate,
  onGone,
}: {
  view: ClientIntakeView
  token: string
  onUpdate: (next: ClientIntakeView) => void
  onGone: () => void
}) {
  if (view.state === 'issued') {
    return <ClientForm view={view} token={token} onUpdate={onUpdate} onGone={onGone} />
  }
  if (view.state === 'waiting') return <ClientWaiting view={view} />
  if (view.state === 'closed') return <ClientClosed />
  // The only member left is `ClientQuotationView` - `'sent'`,
  // `'revision_requested'` or `'finalized'`.
  return <ClientQuotation view={view} token={token} onUpdate={onUpdate} onGone={onGone} />
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

