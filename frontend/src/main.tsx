import { Component, StrictMode } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { createRoot } from 'react-dom/client'

// The design system first, so every rule is in place before anything paints.
import './index.css'
import App from './App'
import AuthGate from './components/AuthGate'
import ClientShell from './components/client/ClientShell'

/**
 * The boot screen's second milestone, reported at the first moment it is true.
 *
 * This line runs when the module has been fetched, parsed and executed — which
 * on a slow connection is the whole wait, and on a fast one happened before
 * anybody looked. It is the only honest way to tell the two apart, because
 * until this point none of our own JavaScript is running to report anything.
 */
window.__prismBoot?.step(1)

type RootErrorBoundaryProps = { children: ReactNode }

/** Whatever was thrown, which is not necessarily an `Error`. */
type RootErrorBoundaryState = { error: unknown }

/**
 * Last line of defence.
 *
 * A single malformed field in a Gemini response should not leave the visitor
 * staring at a blank page with the real reason buried in the console. This
 * catches whatever escaped the component tree, says what happened, and offers
 * the only move that helps.
 */
class RootErrorBoundary extends Component<RootErrorBoundaryProps, RootErrorBoundaryState> {
  constructor(props: RootErrorBoundaryProps) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: unknown): RootErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('PRISM stopped rendering.', error, info?.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    // What was thrown is `unknown`, so its message is read off it by hand. The
    // `|| error` behind it is the case this whole expression exists for: what
    // came through was not an Error at all, and printing it beats printing
    // nothing.
    const detail = String(
      (typeof error === 'object' && error !== null && 'message' in error ? error.message : null) ||
        error ||
        '',
    ).trim()

    return (
      <div role="alert" className="mx-auto max-w-sheet px-6 py-16">
        <p className="mono text-xs uppercase tracking-[0.2em] text-void">Render fault</p>

        <h1 className="display mt-3 text-xl">The page stopped drawing</h1>

        <p className="mt-4 max-w-[62ch] text-base">
          Something in the last response was shaped in a way this page could not render. The
          quotation itself is fine on the server — reload to pick it up again, and check the browser
          console for the exact line.
        </p>

        {detail ? (
          <pre className="mono mt-5 max-w-full overflow-x-auto rounded border border-rule bg-duplicate px-4 py-3 text-sm">
            {detail}
          </pre>
        ) : null}

        <button
          type="button"
          onClick={() => window.location.reload()}
          className="display mt-7 cursor-pointer rounded border-2 border-ballpoint px-5 py-2.5 text-xs text-ballpoint"
        >
          Reload PRISM
        </button>
      </div>
    )
  }
}

/**
 * Where a client's own link points - `#/c/<token>`, minted by
 * `backend/app/main.py`'s `_client_link` and handed out nowhere else. Read
 * once, here, before anything downstream of `AuthGate` gets a chance to run.
 *
 * This is the one structural rule this file exists to keep: `App.tsx` fires
 * `listWorkspaces()` and `fetchSettings()` unconditionally from its own
 * `useEffect`s the moment it mounts, and both answer a signed-out caller
 * with 401. `AuthGate` fires its own sign-in check the same way. A client
 * holding this link has no studio session and is not signed in to anything -
 * so `<ClientShell>` has to stand in for `<AuthGate><App/></AuthGate>`
 * *before* either one is ever mounted, not be admitted past the gate by an
 * allowlist inside it. An allowlist runs too late: by the time `AuthGate`
 * would decide to let a client-shaped hash through, `App` has already
 * mounted somewhere beneath it and its effects have already fired the two
 * calls this rule exists to prevent.
 *
 * The token's own character set is `secrets.token_urlsafe(24)`'s - see
 * `backend/app/tokens.py` - which never emits anything outside
 * `[A-Za-z0-9_-]`. This match is intentionally on the raw hash, computed
 * once at load: like the rest of this file, boot is a one-time decision, not
 * something that re-runs as the hash changes later.
 */
const CLIENT_LINK = /^#\/c\/([A-Za-z0-9_-]{1,300})$/
const clientToken = CLIENT_LINK.exec(window.location.hash || '')?.[1] || ''

const container = document.getElementById('root')

if (!container) {
  throw new Error('PRISM could not start: index.html has no <div id="root"> to mount into.')
}

createRoot(container).render(
  <StrictMode>
    <RootErrorBoundary>
      {clientToken ? (
        // A stranger's own link, resolved with no session at all - see
        // CLIENT_LINK's own comment above for why this branch has to come
        // ahead of AuthGate rather than live inside it.
        <ClientShell token={clientToken} />
      ) : (
        // Who is asking, before anything is shown - on the installs that ask.
        // Where no project is configured the gate renders the app untouched.
        <AuthGate>
          <App />
        </AuthGate>
      )}
    </RootErrorBoundary>
  </StrictMode>,
)

/**
 * Take the boot screen down once the app has actually painted.
 *
 * The pacing and the removal both live in the inline script in index.html, so
 * that a boot screen shown before this bundle existed is also the thing that
 * takes it away. All that is left here is the truth this file is the first to
 * know: React has rendered.
 *
 * There used to be a 1500ms floor here, defended on the grounds that the mark
 * is a sequence worth seeing. It also meant a local start that was ready in
 * 200ms sat behind a full second of nothing, and a start that took eight
 * seconds looked exactly the same. The wait is now as long as the load is.
 */
requestAnimationFrame(() => {
  window.__prismBoot?.step(2)
  window.__prismBoot?.done()
})
