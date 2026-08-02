import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createProposal,
  currentWorkspace,
  fetchBundle,
  fetchIntake,
  fetchSettings,
  listWorkspaces,
  reviseProposal,
  setCurrentWorkspace,
} from './lib/api'
import type { ApiErrorKind, ProposalChanges } from './lib/api'
import { useJobWatch } from './lib/useJobWatch'
import { noteNavigation } from './lib/navigation'
import SettingsPanel from './components/SettingsPanel'
import QuotationList from './components/QuotationList'
import JobList from './components/JobList'
import HomeScreen from './components/HomeScreen'
import ProposalStudio from './components/ProposalStudio'
import ProposalList from './components/ProposalList'
import WorkspacesScreen from './components/WorkspacesScreen'
import IntakeScreen from './components/IntakeScreen'
import IntakeListScreen from './components/IntakeListScreen'
import ProfileScreen from './components/ProfileScreen'
import TeamScreen from './components/TeamScreen'
import InviteScreen from './components/InviteScreen'
import { useRole } from './lib/role'
import ProposalView from './components/ProposalView'
import AppHeader from './components/AppHeader'
import QuotationHeader from './components/QuotationHeader'
import BriefForm from './components/BriefForm'
import ResultSheets from './components/ResultSheets'
import RevisePanel from './components/RevisePanel'
import QuotationNotice from './components/QuotationNotice'
import TierSwitcher from './components/TierSwitcher'
import ErrorNotice from './components/ErrorNotice'
import { formatDate } from './lib/format'
import { prefersReducedMotion } from './components/motion'
import type { Intake, ProposalBundle, StudioDefaults, Workspace } from './types'

/** Every screen this router can name. `routeFor` answers with one of them. */
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

/** Which screen a hash points at. Anything unrecognised is the front page. */
function routeFor(hash: string): Route {
  // Plain string matching rather than a regex: the route names are literals and
  // a pattern here buys nothing but escaping bugs.
  const value = String(hash || '').replace(/^#/, '')
  if (value === '/settings' || value.startsWith('/settings/')) return 'settings'
  if (value === '/quotations' || value.startsWith('/quotations/')) return 'quotations'
  if (value === '/jobs' || value.startsWith('/jobs/')) return 'jobs'
  if (value === '/pad' || value.startsWith('/pad/')) return 'pad'
  if (value === '/proposals' || value.startsWith('/proposals/')) return 'proposals'
  if (value === '/documents' || value.startsWith('/documents/')) return 'documents'
  if (value === '/workspaces' || value.startsWith('/workspaces/')) return 'workspaces'
  if (value === '/teams' || value.startsWith('/teams/')) return 'teams'
  // The specific path first: '/intakes/new'.startsWith('/intakes') is true, so
  // the generic check below would otherwise claim it before this one runs.
  if (value === '/intakes/new') return 'intakeNew'
  if (value === '/intakes' || value.startsWith('/intakes/')) return 'intakes'
  if (value === '/profile' || value.startsWith('/profile/')) return 'profile'
  if (value.startsWith('/invite/')) return 'invite'
  // A built proposal reads on its own page, like a quotation does.
  if (value.startsWith('/p/')) return 'proposal'
  // A prepared quotation reads on its own page, without the form that made it.
  if (value.startsWith('/q/')) return 'quotation'
  // The settings screen used to live at /admin. A bookmark from then still
  // works rather than silently landing somewhere else.
  if (value === '/admin' || value.startsWith('/admin/')) return 'settings'
  return 'home'
}

/** What the close button on each screen is closing. */
const SCREEN_NAME: Partial<Record<Route, string>> = {
  pad: 'Create PAD',
  jobs: 'In progress',
  quotations: 'PAD Quotations',
  proposals: 'Build Proposal',
  documents: 'Proposals',
  workspaces: 'Workspaces',
  teams: 'Teams',
  intakes: 'Client requests',
  intakeNew: 'New client request',
  profile: 'Profile',
  invite: 'Invitation',
  proposal: 'Proposal',
  settings: 'Settings',
  quotation: 'Quotation',
}

const DEEP_LINK = /^#\/q\/([A-Za-z0-9._-]{1,120})$/

/**
 * What this file reads off a rejected value.
 *
 * Everything is optional and all but one field is `unknown`: this is three
 * libraries' idea of an error rather than a shape anything promises. `kind` is
 * typed, because `describeError` branches on five of `ApiError`'s values and a
 * typo in one of them would otherwise compile and quietly never match.
 */
type ErrorLike = {
  status?: unknown
  statusCode?: unknown
  response?: { status?: unknown } | null
  detail?: unknown
  message?: unknown
  kind?: ApiErrorKind
}

function statusOf(error: unknown): number {
  if (!error || typeof error !== 'object') return 0
  // Past the guard this is an object; a field it does not carry reads as
  // undefined, which is exactly what the search below looks past.
  const carrier = error as ErrorLike
  const candidates = [
    carrier.status,
    carrier.statusCode,
    carrier.response && carrier.response.status,
  ]
  const found = candidates.find((value): value is number => typeof value === 'number' && value > 0)
  return found || 0
}

function messageOf(error: unknown): string {
  if (!error) return ''
  if (typeof error === 'string') return error.trim()
  // Read the same way `statusOf` reads a status: two fields that may or may
  // not be there.
  const carrier = error as ErrorLike
  return String(carrier.detail || carrier.message || '').trim()
}

/**
 * One failure as the page states it: the headline, the next move, and the
 * status code when there was one. Exported because the revision slip shows the
 * same three fields on its own card.
 */
export type ErrorReport = {
  code: number | null
  headline: string
  next: string
}

/**
 * Turn whatever came back into a sentence about what happened and a sentence
 * about the next move. Nothing here apologises, and nothing here shows a
 * traceback to someone who cannot act on it.
 *
 * `ApiError` from lib/api already carries an actionable message and a `kind`;
 * this adds the headline that says which of the handful of things went wrong,
 * and overrides the message in the one case docs/DESIGN.md scripts word for
 * word — the missing Gemini key.
 */
function describeError(error: unknown): ErrorReport | null {
  const status = statusOf(error)
  // Whatever was thrown might carry an `ApiError`'s `kind`, and might be
  // nothing of the sort.
  const carrier = error as ErrorLike | null
  const kind = (carrier && carrier.kind) || ''
  const raw = messageOf(error)

  if (kind === 'aborted') return null

  if (/gemini_api_key/i.test(raw) || (status === 503 && !raw)) {
    return {
      code: status || 503,
      headline: 'Gemini key not configured.',
      next: 'Add GEMINI_API_KEY to backend/.env and restart the API.',
    }
  }

  // A 503 that names something other than the key is a different missing
  // dependency, and saying "add the key" would send the reader the wrong way.
  if (status === 503) {
    return { code: 503, headline: 'The API is running but not configured.', next: raw }
  }

  if (kind === 'network') {
    return {
      code: null,
      headline: 'The API is not answering on port 8000.',
      next: raw,
    }
  }

  if (kind === 'timeout') {
    return {
      code: null,
      headline: 'The API stopped answering.',
      next: raw,
    }
  }

  if (kind === 'parse') {
    return {
      code: status || null,
      headline: 'That answer was not a quotation.',
      next: raw,
    }
  }

  if (kind === 'validation') {
    return {
      code: null,
      headline: 'That cannot be sent yet.',
      next: raw,
    }
  }

  if (status === 404) {
    return { code: 404, headline: 'That quotation is not on file.', next: raw }
  }

  if (status === 429) {
    return { code: 429, headline: 'The Gemini quota is used up.', next: raw }
  }

  if (status >= 500) {
    return { code: status, headline: 'The API failed while preparing the quotation.', next: raw }
  }

  if (status >= 400) {
    return { code: status, headline: 'The API rejected the request.', next: raw }
  }

  return {
    code: status || null,
    headline: 'The quotation did not come back.',
    next: raw || 'Check the API window for what it reported, then prepare the quotation again.',
  }
}

export default function App() {
  const [bundle, setBundle] = useState<ProposalBundle | null>(null)
  const [error, setError] = useState<ErrorReport | null>(null)
  const [pending, setPending] = useState(false)
  const [restoring, setRestoring] = useState(false)
  // A failed revision is reported on the revision slip itself, not at the top
  // of the page: the quotation on screen is still perfectly valid, and moving
  // the error away from the control that produced it would suggest otherwise.
  const [revising, setRevising] = useState(false)
  const [reviseError, setReviseError] = useState<ErrorReport | null>(null)
  // Three screens, one hash router: the pad you work in, the quotations already
  // sent, and the settings behind both.
  const [route, setRoute] = useState(() => routeFor(window.location.hash))
  // The raw hash, kept as its own piece of state rather than re-read from
  // `window.location.hash` during render. `route` is a *category* -
  // `#/pad/A -> #/pad/B` is the same category, `'pad'`, both times - so
  // `setRoute('pad')` is a same-value update and React 18 bails out of
  // re-rendering on account of it. `hash` genuinely changes on every one of
  // those transitions, so it is what makes `padIntakeId` below actually
  // reactive instead of frozen at whichever id first opened the pad. Updated
  // in the same `followRoute` that already runs on mount and on `hashchange`.
  const [hash, setHash] = useState(() => window.location.hash || '')
  // Studio defaults prefill the brief form. They arrive after first paint, so
  // the form mounts once they are known rather than resetting under the user.
  // Partial, because the catch below opens the form on nothing at all.
  const [defaults, setDefaults] = useState<Partial<StudioDefaults> | null>(null)
  // Which client request the pad was opened from. Derived from the reactive
  // `hash` state above, not `window.location.hash` directly, and gated by
  // recomputing the route from that same `hash` rather than trusting the
  // `route` state variable - so this is correct even in the one render where
  // `route`'s own update bailed out. Covers `#/pad/A -> #/pad`,
  // `#/pad/A -> #/pad/B` and `#/pad/A -> #/pad/`, all reachable in-app via the
  // command bar's Create PAD entry while a prefilled pad is already open.
  const padIntakeId = routeFor(hash) === 'pad' ? hash.replace(/^#\/pad\/?/, '') : ''
  // The request the pad is prefilled from, once it has been fetched. A stale
  // or unknown id is not a reason to refuse the pad — it opens empty, which is
  // the screen the studio would otherwise have gone to anyway.
  const [intake, setIntake] = useState<Intake | null>(null)
  // How many books of work exist. `null` while it is being read; 0 means the
  // app has nowhere to file anything, which is the one state that overrides
  // whatever screen the address bar asks for.
  const [workspaceCount, setWorkspaceCount] = useState<number | null>(null)
  // What this person may do here. The server refuses the rest either way;
  // this is so the app does not offer it.
  const { isAdmin } = useRole()
  const resultsRef = useRef<HTMLDivElement | null>(null)
  const shouldScroll = useRef(false)
  // The quotation the address bar is currently pointing at.
  const shownId = useRef('')
  // Where the reader is right now, readable from inside a job callback that was
  // created a minute and a half ago. A finished job must not drag someone off
  // the page they walked to while it ran.
  const routeNow = useRef(route)
  routeNow.current = route
  // Which quotation asked for the revision that is currently running.
  const revisingFrom = useRef('')

  // A prepared quotation is addressable, so a reload or a shared link brings it
  // straight back instead of costing another Gemini pass. Pasting such a link
  // into the address bar of a page that is already open only changes the hash,
  // which is not a page load — hence the listener as well as the mount pass.
  useEffect(() => {
    let live = true

    const restore = () => {
      const match = DEEP_LINK.exec(window.location.hash || '')
      // Leaving the deep link (Back, or the wordmark) keeps whatever is on
      // screen; only a different quotation is worth a round trip.
      if (!match || match[1] === shownId.current) return
      shownId.current = match[1]

      setRestoring(true)
      Promise.resolve()
        .then(() => fetchBundle(shownId.current))
        .then((restored) => {
          if (!live) return
          setBundle(restored)
          setError(null)
        })
        .catch((failure: unknown) => {
          if (!live) return
          // The address bar now points at a quotation that did not load.
          // Leaving the previous one on screen would put three different
          // answers on the page at once: the URL, the notice and the sheets.
          setBundle(null)
          setError(describeError(failure))
        })
        .finally(() => {
          if (live) setRestoring(false)
        })
    }

    const followRoute = () => {
      setRoute(routeFor(window.location.hash))
      setHash(window.location.hash || '')
      restore()
    }

    followRoute()
    window.addEventListener('hashchange', followRoute)
    return () => {
      live = false
      window.removeEventListener('hashchange', followRoute)
    }
  }, [])

  /**
   * How many books of work this person has — and whether the one the browser
   * remembers is still one of them.
   *
   * The stored workspace is a property of the browser, and the account is not.
   * Sign in as somebody else on the same machine and the app would carry on
   * naming the previous person's workspace: the server answers 403 to every
   * request for a team you are not on, so the whole app reads as broken rather
   * than as simply not yours.
   *
   * `/api/workspaces` answers with the ones you are actually on, so it is the
   * authority here. A stored id missing from that list is stale, and what
   * happens next depends on what the person has:
   *
   *   · nothing at all → forget it, and the screen below asks them to name one
   *   · exactly one    → open it, because there is no choice to offer
   *   · several        → the workspaces page, so they pick rather than land
   *                      somewhere arbitrary
   *
   * The reload is deliberate: by this point other screens have already asked
   * the API with the wrong workspace on the header, and re-fetching them one by
   * one is more moving parts than starting again with the right one.
   */
  useEffect(() => {
    let live = true
    listWorkspaces()
      .then((found) => {
        if (!live) return
        setWorkspaceCount(found.length)

        const chosen = currentWorkspace()
        if (chosen && found.some((workspace) => workspace.id === chosen)) return

        if (found.length === 0) {
          if (chosen) setCurrentWorkspace('')
          return
        }

        if (found.length === 1) {
          setCurrentWorkspace(found[0].id)
          window.location.reload()
          return
        }

        setCurrentWorkspace('')
        window.location.hash = '#/workspaces'
      })
      // Unreadable is not the same as none. Assume there is one and let the
      // screens report their own errors, rather than sending somebody to name a
      // workspace because the API was briefly down.
      .catch(() => live && setWorkspaceCount(1))
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    let live = true
    fetchSettings()
      .then((saved) => live && setDefaults(saved))
      // Defaults are a convenience. If they cannot be read the form still works,
      // it just opens on PHP/Philippines like it always did.
      .catch(() => live && setDefaults({}))
    return () => {
      live = false
    }
  }, [])

  // "Price this" on the request queue links to `#/pad/<id>` with the client's
  // own words already on file. Fetching it here, rather than inside the pad
  // route below, means it only ever runs once per id — not once per render of
  // a screen that re-renders on every keystroke.
  useEffect(() => {
    // Cleared the moment the id changes, not just when it disappears - moving
    // from one prefilled pad straight to another (`#/pad/A -> #/pad/B`) must
    // not leave A's record sitting in state, associated with B's id, for the
    // moment it takes the new fetch to land.
    setIntake(null)
    if (!padIntakeId) return
    let live = true
    fetchIntake(padIntakeId)
      .then((found) => live && setIntake(found))
      // A stale link is not a reason to refuse the pad. It opens empty, which
      // is the screen the studio would otherwise have gone to anyway.
      .catch(() => live && setIntake(null))
    return () => {
      live = false
    }
  }, [padIntakeId])

  useEffect(() => {
    if (!bundle || !shouldScroll.current || !resultsRef.current) return
    shouldScroll.current = false
    resultsRef.current.scrollIntoView({
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
      block: 'start',
    })
  }, [bundle])

  /**
   * Open a finished job's first quotation — but only if the reader is still
   * where they were when they started it.
   *
   * A quotation takes ninety seconds. In that time somebody can quite
   * reasonably walk to Settings, or to the jobs page, or open an older sheet.
   * Yanking them to a new page because a background task finished would punish
   * exactly the behaviour background jobs exist to allow. When they have moved
   * on, the work simply sits on the jobs page marked Ready.
   */
  const showResult = useCallback((resultIds: string[], from: Route, replace: boolean) => {
    // The route check comes first, including for a job that produced nothing:
    // an error raised for a page nobody is looking at would surface minutes
    // later, next to whatever they were doing by then. The jobs page carries
    // the outcome either way.
    if (routeNow.current !== from) return

    const id = resultIds[0]
    if (!id) {
      setError({
        code: null,
        headline: 'That job finished without a quotation in it.',
        next: 'Check the API window — the run reported no documents.',
      })
      return
    }

    fetchBundle(id)
      .then((prepared) => {
        setBundle(prepared)
        shouldScroll.current = true
        shownId.current = prepared.id
        // replaceState does not fire hashchange, so the route is set by hand —
        // and so is the back-stack position, which is fed by hashchange too.
        if (replace) window.history.replaceState(null, '', `#/q/${prepared.id}`)
        else window.history.pushState(null, '', `#/q/${prepared.id}`)
        noteNavigation({ replace })
        setRoute('quotation')
      })
      .catch((failure: unknown) => setError(describeError(failure)))
  }, [])

  const creation = useJobWatch({
    onDone: (resultIds) => {
      setPending(false)
      // Pushed, not replaced. Replacing consumed the pad's history entry, so
      // closing the quotation that had just been prepared went to the front
      // page instead of back to the pad it came from — and from a browser
      // opened straight at #/pad there was nothing of ours behind it at all.
      showResult(resultIds, 'pad', false)
    },
    onFail: (job) => {
      setPending(false)
      setError({
        code: null,
        headline: 'The quotation was not prepared.',
        next: job.error || 'Check the API window for what it reported, then try again.',
      })
    },
  })

  const revision = useJobWatch({
    onDone: (resultIds) => {
      setRevising(false)
      // The revision replaces what is on screen and takes over the address
      // bar. The parent is untouched on the server, and QuotationNotice links
      // back to it.
      //
      // Only if the sheet on screen is still the one that asked for it. Opening
      // a different quotation while a revision runs is the same move as walking
      // to another page, and it deserves the same answer: leave the reader
      // where they are and let them open the revision from the jobs page.
      if (shownId.current !== revisingFrom.current) return
      showResult(resultIds, 'quotation', false)
    },
    onFail: (job) => {
      setRevising(false)
      setReviseError({
        code: null,
        headline: 'The revision was not prepared.',
        next: job.error || 'Check the API window for what it reported, then try again.',
      })
    },
  })

  // The hooks hand back a fresh object each render; the two callbacks below are
  // stable, and depending on those rather than the objects keeps the submit
  // handlers from being rebuilt on every keystroke in the form.
  const { watch: watchCreation } = creation
  const { watch: watchRevision } = revision

  const handleSubmit = useCallback(
    (formData: FormData) => {
      if (pending) return
      // Set before the request leaves, not when the job comes back: the 202
      // takes a moment, and a second click inside it is a second Gemini pass.
      setPending(true)
      setError(null)

      Promise.resolve()
        .then(() => createProposal(formData))
        .then((job) => watchCreation(job))
        .catch((failure: unknown) => {
          setPending(false)
          setError(describeError(failure))
        })
    },
    [pending, watchCreation],
  )

  const handleRevise = useCallback(
    (changes: ProposalChanges) => {
      if (revising || !bundle) return
      setRevising(true)
      setReviseError(null)
      revisingFrom.current = bundle.id

      Promise.resolve()
        .then(() => reviseProposal(bundle.id, changes))
        .then((job) => watchRevision(job))
        .catch((failure: unknown) => {
          setRevising(false)
          setReviseError(describeError(failure))
        })
    },
    [bundle, revising, watchRevision],
  )

  // One line above every screen: whose quotations these are, which screen you
  // are on, and the way out of it. It replaced four navigation links that were
  // on screen at all times, three of which always pointed somewhere you had
  // chosen not to be.
  const nav = (
    <AppHeader
      screenName={SCREEN_NAME[route] || ''}
      studioName={defaults?.studio_name || ''}
      onClose={route === 'home' ? '' : '#/'}
    />
  )

  // The working screens are pinned to the viewport like the pad. They are
  // places you do something in - find a quotation, watch a job, change a rate -
  // and the thing you came to act on should be on screen when you arrive.
  // Each page scrolls its own list, so the header and the actions stay put.
  // With no workspace there is nowhere to put a quotation, so nothing else is
  // worth showing: naming one is the only useful thing on the screen.
  if (workspaceCount === 0 || route === 'workspaces') {
    return (
      <div className="h-dvh overflow-hidden bg-canvas font-body text-body">
        <main className="mx-auto flex h-full w-full max-w-sheet flex-col px-4 py-6 sm:px-6 sm:py-8">
          {workspaceCount === 0 ? null : nav}
          <WorkspacesScreen onChanged={(found: Workspace[]) => setWorkspaceCount(found.length)} />
        </main>
      </div>
    )
  }

  if (
    route === 'quotations' ||
    route === 'settings' ||
    route === 'jobs' ||
    route === 'proposals' ||
    route === 'documents' ||
    route === 'profile' ||
    route === 'teams' ||
    route === 'intakes' ||
    route === 'intakeNew' ||
    route === 'invite' ||
    route === 'proposal'
  ) {
    return (
      <div className="h-dvh overflow-hidden bg-canvas font-body text-body">
        <main className="mx-auto flex h-full w-full max-w-sheet flex-col px-4 py-6 sm:px-6 sm:py-8">
          {nav}
          {route === 'quotations' ? <QuotationList /> : null}
          {route === 'jobs' ? <JobList /> : null}
          {route === 'proposals' ? <ProposalStudio /> : null}
          {route === 'documents' ? <ProposalList /> : null}
          {route === 'profile' ? <ProfileScreen /> : null}
          {route === 'teams' ? <TeamScreen /> : null}
          {route === 'intakes' ? <IntakeListScreen /> : null}
          {route === 'intakeNew' ? <IntakeScreen /> : null}
          {route === 'invite' ? (
            <InviteScreen token={(window.location.hash || '').replace('#/invite/', '')} />
          ) : null}
          {route === 'proposal' ? (
            <ProposalView documentId={(window.location.hash || '').replace('#/p/', '')} />
          ) : null}
          {route === 'settings' ? (
            isAdmin ? (
              <SettingsPanel />
            ) : (
              <div className="rounded-xl border border-rule bg-paper px-6 py-8 shadow-sheet">
                <p className="font-label text-[12px] uppercase tracking-[0.14em] text-faint">
                  Not yours to change
                </p>
                <p className="mt-2 max-w-[60ch] font-body text-[15px] leading-[1.6] text-void">
                  Settings belongs to this workspace’s admins — the rate card, the terms and the
                  design are what every quotation is quoted and printed from. Ask one of them if
                  something needs changing.
                </p>
                <a href="#/" className="mt-4 inline-block font-body text-[14px] text-ballpoint">
                  Back to the front page
                </a>
              </div>
            )
          ) : null}
        </main>
      </div>
    )
  }

  // A prepared quotation gets its own page. Reading one and writing one are
  // different jobs: leaving the brief form above a finished quotation invited
  // the reader to edit the thing they were trying to read, and pushed the
  // documents below the fold on every visit.
  if (route === 'quotation') {
    return (
      <div className="min-h-screen bg-canvas font-body text-body">
        <main className="mx-auto w-full max-w-sheet px-4 py-10 sm:px-6 sm:py-16">
          {nav}

          <div className="mb-8 rounded-xl border border-rule bg-paper shadow-sheet">
            <QuotationHeader
              reference={bundle?.estimate?.quotation_ref || ''}
              quotationId={bundle ? bundle.id : ''}
              issued={bundle && bundle.created_at ? formatDate(bundle.created_at) : ''}
            />
          </div>

          {error ? (
            <div className="mb-6">
              <ErrorNotice
                headline={error.headline}
                next={error.next}
                code={error.code}
                onDismiss={() => setError(null)}
              />
            </div>
          ) : null}

          {restoring && !bundle ? (
            <p
              aria-live="polite"
              className="px-4 py-14 text-center font-label text-[12px] uppercase tracking-[0.14em] text-faint sm:px-8"
            >
              Retrieving quotation
            </p>
          ) : null}

          {bundle ? (
            <>
              <TierSwitcher bundle={bundle} />
              <QuotationNotice bundle={bundle} />
              <ResultSheets key={bundle.id} bundle={bundle} />
              <RevisePanel
                key={`revise-${bundle.id}`}
                bundle={bundle}
                pending={revising}
                job={revision.job}
                error={reviseError}
                onRevise={handleRevise}
              />
            </>
          ) : null}
        </main>
      </div>
    )
  }

  if (route === 'home') {
    return (
      <div className="h-dvh overflow-hidden bg-canvas font-body text-body">
        <main className="mx-auto flex h-full w-full max-w-sheet flex-col px-4 py-6 sm:px-6 sm:py-8">
          {nav}
          <HomeScreen />
        </main>
      </div>
    )
  }

  // The pad is the one screen in this app that does not scroll. It is a form
  // you work in rather than a document you read, and a form whose card changed
  // height on every step made the button you were reaching for move. So the
  // page is pinned to the viewport, the card fills what is left of it, and the
  // only thing that can overflow is the step panel inside — silently, without a
  // bar, because the card is already the same height it was a moment ago.
  return (
    <div className="h-dvh overflow-hidden bg-canvas font-body text-body">
      <main className="mx-auto flex h-full w-full max-w-sheet flex-col px-4 py-6 sm:px-6 sm:py-8">
        {nav}
        <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-rule bg-paper shadow-sheet">
          <QuotationHeader
            title="New PAD Quotation"
            blurb="Describe the job once. PRISM prices it and writes both documents."
          />
          {defaults ? (
            <BriefForm
              // Remounts whenever the underlying request changes identity -
              // including the transition into and out of "no request at
              // all" - so a second `#/pad/<id>` (or a plain `#/pad`) reached
              // while the pad is already open gets a genuinely fresh form,
              // not the previous one's fields with a new id spliced in. This
              // is also what lets the `seeded` latch in BriefForm re-arm for
              // a second request: the latch never resets on its own, a fresh
              // mount is a fresh ref.
              key={intake ? intake.id : 'blank'}
              defaults={defaults}
              pending={pending}
              job={creation.job}
              onSubmit={handleSubmit}
              intakeId={intake ? intake.id : ''}
              prefill={
                intake
                  ? {
                      // Coerced rather than trusted: `fetchIntake` only checks
                      // that `id` is a string, so a server answer with `scope`
                      // or the others coming back `null` must not hand
                      // `BriefForm` anything but a string to seed a text field
                      // with.
                      scope: String(intake.scope ?? ''),
                      budget: String(intake.budget_text ?? ''),
                      clientName: String(intake.client_email ?? ''),
                    }
                  : undefined
              }
            />
          ) : null}
        </div>

        {error ? (
          <div ref={resultsRef} className="mt-4 flex-none">
            <ErrorNotice
              headline={error.headline}
              next={error.next}
              code={error.code}
              onDismiss={() => setError(null)}
            />
          </div>
        ) : null}
      </main>
    </div>
  )
}
