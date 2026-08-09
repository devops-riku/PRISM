# PRISM frontend routes

## Routing model

- Framework: React 18 + TypeScript, built with Vite 6.
- Router: custom hash routing; there is no React Router or file-based router.
- Server pathname: the production Nginx `location /` rule falls back to `/index.html`, and Vite serves that same SPA entry in development.
- App route source: `frontend/src/App.tsx` reads `window.location.hash`, classifies it with `routeFor`, and listens for `hashchange`.
- Pre-router dispatch: `frontend/src/main.tsx` recognizes an exact `#/c/<token>` before either `AuthGate` or `App` mounts. Client links therefore do not make authenticated workspace API calls.
- Authentication: every non-client-link request passes through `frontend/src/components/AuthGate.tsx`. If authentication is disabled, or a session exists, it renders `App`; if authentication is required and there is no user, it renders `LandingScreen` with `AuthScreen` embedded.
- Unknown hashes: there is no not-found screen. Any hash not matched by `routeFor` is the authenticated home route.

## Root landing and authentication behavior

The browser pathname `/` is the public landing location; it is not a separate hash route.

| Address/state | Rendered component | Layout |
| --- | --- | --- |
| `/` with no hash, auth required, signed out | `frontend/src/components/LandingScreen.tsx` | Standalone responsive landing; its right column contains `AuthScreen` |
| `/` with no hash, auth required, signed in | `frontend/src/components/HomeScreen.tsx` through `App` | Authenticated pinned app shell with `AppHeader` |
| `/` with no hash, auth disabled | `frontend/src/components/HomeScreen.tsx` through `App` | Authenticated-style pinned app shell without an account gate |
| `/#/c/<token>` | `frontend/src/components/client/ClientShell.tsx` | Standalone client portal; bypasses both `AuthGate` and `App` |

Requested landing invariant for the current redesign: make the clean, empty-hash `/` URL the public landing for every visitor rather than adding `#/landing`. The landing should lead into the studio at `/#/`; that hash route remains behind `AuthGate` and can show `AuthScreen` when a session is required. Existing signed-in studio behavior, internal hashes, invitation hashes, and the pre-router client-token branch remain unchanged. The landing diagram input must be labelled **Your Scope**. Authentication should be a clear landing action, not an embedded card that dominates the product story.

## Pre-router client-link source

There is no standalone router configuration file. This is the complete client-token dispatch rule from `frontend/src/main.tsx`:

```tsx
const CLIENT_LINK = /^#\/c\/([A-Za-z0-9_-]{1,300})$/
const clientToken = CLIENT_LINK.exec(window.location.hash || '')?.[1] || ''

createRoot(container).render(
  <StrictMode>
    <RootErrorBoundary>
      {clientToken ? (
        <ClientShell token={clientToken} />
      ) : (
        <AuthGate>
          <App />
        </AuthGate>
      )}
    </RootErrorBoundary>
  </StrictMode>,
)
```

## Hash router source

There is no separate `router.ts` or route-object config. The complete route type and classifier from `frontend/src/App.tsx` are:

```tsx
type Route =
  | 'settings'
  | 'quotations'
  | 'jobs'
  | 'pad'
  | 'proposals'
  | 'documents'
  | 'workspaces'
  | 'teams'
  | 'intakes'
  | 'intakeNew'
  | 'profile'
  | 'invite'
  | 'proposal'
  | 'quotation'
  | 'home'

function routeFor(hash: string): Route {
  const value = String(hash || '').replace(/^#/, '')
  if (value === '/settings' || value.startsWith('/settings/')) return 'settings'
  if (value === '/quotations' || value.startsWith('/quotations/')) return 'quotations'
  if (value === '/jobs' || value.startsWith('/jobs/')) return 'jobs'
  if (value === '/pad' || value.startsWith('/pad/')) return 'pad'
  if (value === '/proposals' || value.startsWith('/proposals/')) return 'proposals'
  if (value === '/documents' || value.startsWith('/documents/')) return 'documents'
  if (value === '/workspaces' || value.startsWith('/workspaces/')) return 'workspaces'
  if (value === '/teams' || value.startsWith('/teams/')) return 'teams'
  if (value === '/intakes/new') return 'intakeNew'
  if (value === '/intakes' || value.startsWith('/intakes/')) return 'intakes'
  if (value === '/profile' || value.startsWith('/profile/')) return 'profile'
  if (value.startsWith('/invite/')) return 'invite'
  if (value.startsWith('/p/')) return 'proposal'
  if (value.startsWith('/q/')) return 'quotation'
  if (value === '/admin' || value.startsWith('/admin/')) return 'settings'
  return 'home'
}
```

`App` initializes from the current hash and keeps both the route category and raw hash reactive:

```tsx
const [route, setRoute] = useState(() => routeFor(window.location.hash))
const [hash, setHash] = useState(() => window.location.hash || '')

useEffect(() => {
  const followRoute = () => {
    setRoute(routeFor(window.location.hash))
    setHash(window.location.hash || '')
    restore()
  }

  followRoute()
  window.addEventListener('hashchange', followRoute)
  return () => window.removeEventListener('hashchange', followRoute)
}, [])
```

## Route map

| Browser URL | Route category | Primary component(s) | Layout and behavior |
| --- | --- | --- | --- |
| `/` with no hash | public landing branch (requested) | `LandingScreen.tsx` | Standalone, light-first marketing landing; no workspace bootstrap or authenticated header |
| `/#/` | `home` after auth opens | `HomeScreen.tsx` | Auth-gated pinned `h-dvh` app shell, `AppHeader`, `max-w-app` main |
| `/#/settings` and descendants | `settings` | `SettingsPanel.tsx` | Pinned app shell; admins see settings, non-admins see an access notice |
| `/#/admin` and descendants | `settings` | `SettingsPanel.tsx` | Legacy alias for settings |
| `/#/quotations` and descendants | `quotations` | `QuotationList.tsx` | Pinned app shell with internally scrolling list |
| `/#/jobs` and descendants | `jobs` | `JobList.tsx` | Pinned app shell with live/polled job progress |
| `/#/pad` | `pad` | `QuotationHeader.tsx`, `BriefForm.tsx`, `ErrorNotice.tsx` | Pinned quotation-form shell; the step panel owns overflow |
| `/#/pad/<intake-id>` | `pad` | Same as `/#/pad`, prefilled from the intake | The raw hash is retained so switching intake IDs remounts and refetches correctly |
| `/#/proposals` and descendants | `proposals` | `ProposalStudio.tsx` | Pinned app shell for building a proposal document from a quotation |
| `/#/documents` and descendants | `documents` | `ProposalList.tsx` | Pinned app shell listing generated proposal documents |
| `/#/workspaces` and descendants | `workspaces` | `WorkspacesScreen.tsx` | Pinned app shell; when no workspace exists this screen overrides every requested App route and omits the header |
| `/#/teams` and descendants | `teams` | `TeamScreen.tsx` | Pinned app shell |
| `/#/intakes` and descendants except exact `/intakes/new` | `intakes` | `IntakeListScreen.tsx` | Pinned app shell listing client requests |
| `/#/intakes/new` | `intakeNew` | `IntakeScreen.tsx` | Pinned app shell; waits for studio defaults before rendering the form |
| `/#/profile` and descendants | `profile` | `ProfileScreen.tsx` | Pinned app shell |
| `/#/invite/<token>` | `invite` | `InviteScreen.tsx` | Pinned app shell; token is read from the hash and passed as a prop |
| `/#/p/<document-id>` | `proposal` | `ProposalView.tsx` | Pinned app shell with proposal-document reader and download actions |
| `/#/q/<quotation-id>` | `quotation` | `QuotationHeader.tsx`, `TierSwitcher.tsx`, `QuotationNotice.tsx`, `ResultSheets.tsx`, `RevisePanel.tsx` | Reading layout: `min-h-screen`, `max-w-sheet`, ordinary document scrolling rather than a pinned viewport |
| `/#/c/<client-token>` | pre-router client branch | `client/ClientShell.tsx` and one of its state screens | Standalone client portal, no `AppHeader`, no auth/workspace bootstrap |
| Any other hash | `home` | `HomeScreen.tsx` | Falls back to the authenticated home; no 404 route |

## Shared layouts

- Authenticated pinned screens: `App.tsx` renders `AppHeader.tsx`, then a `max-w-app` main region. Each screen manages its own scrolling.
- Quotation reader: `App.tsx` renders `AppHeader.tsx`, then a naturally scrolling `max-w-sheet` document region.
- Public landing: `LandingScreen.tsx` owns the complete viewport/page layout at empty-hash `/`; in the requested redesign it links to the auth-gated studio at `/#/` rather than embedding the full `AuthScreen` card.
- Client portal: `client/ClientShell.tsx` owns its own state-dependent shell and never renders studio navigation.
- Root error boundary and boot overlay: `main.tsx` and `index.html`; these wrap every route but are not page-layout components.
