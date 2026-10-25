import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { User } from '../lib/auth'
import { authConfig, currentUser, finishProviderReturn, onSession, startSession } from '../lib/auth'
import { describeAuthError } from '../lib/authErrors'
import { rememberOAuthFailure, takeProviderReceipt } from '../lib/oauthReturn'
import AuthScreen from './AuthScreen'

/**
 * Nothing is shown until the question "who is this?" has an answer — on the
 * installs that ask it.
 *
 * An install with no sign-in configured has no accounts, the API answers
 * everyone, and this renders the app untouched. Putting a sign-in screen in
 * front of an API that would answer a stranger anyway is theatre, and the one
 * thing an auth screen must never be is decorative.
 *
 * Where accounts do exist, the gate holds until the session this browser kept
 * has been restored (and refreshed, if it was about to run out), and until a
 * Google or Facebook return has been finished. That wait is deliberately
 * silent for the first moment: a sign-in card that flashes up and vanishes
 * because a session was there all along reads as having been signed out.
 */

/** The four answers to "who is this?", including not knowing yet. */
type GateState = 'reading' | 'open' | 'misconfigured' | 'gated'

type AuthGateProps = { children: ReactNode }

export default function AuthGate({ children }: AuthGateProps) {
  const [state, setState] = useState<GateState>('reading')
  const [user, setUser] = useState<User | null>(null)

  useEffect(() => {
    let live = true

    authConfig()
      .then(async (found) => {
        if (!live) return
        if (!found.required) {
          setState('open')
          return
        }
        if (!found.ready) {
          // The server requires a sign-in but cannot perform one. Saying so
          // beats an endless spinner, or a form whose every button fails.
          setState('misconfigured')
          return
        }
        const receipt = takeProviderReceipt()
        if (receipt) {
          await finishProviderReturn(receipt.id, receipt.token).catch((failure: unknown) => {
            const problem = describeAuthError(failure)
            rememberOAuthFailure({ code: 'PROVIDER_FAILED', message: problem.message })
          })
        }
        await startSession()
        if (!live) return
        setUser(currentUser())
        setState('gated')
      })
      .catch(() => live && setState('open'))

    const stop = onSession((session) => {
      if (!live) return
      setUser(session?.user || null)
    })

    return () => {
      live = false
      stop()
    }
  }, [])

  if (state === 'reading') {
    return <div className="h-dvh bg-canvas" aria-busy="true" />
  }

  if (state === 'misconfigured') {
    return (
      <div className="flex h-dvh items-center justify-center bg-canvas px-6 font-body text-body">
        {/* p-7 sm:p-8 — the single-card family's shared padding (AuthGate,
            AuthScreen, ClientClosed, ClientQuotation, ClientShell's offline
            face, ClientWaiting, InviteScreen). ClientForm is the one
            exception, fitted to a fixed viewport budget — see its own
            comment. */}
        <div className="max-w-[34rem] rounded-[18px] border border-rule bg-paper p-7 shadow-raised sm:p-8">
          <p className="font-label text-[12px] uppercase tracking-[0.14em] text-alert">
            Sign-in not finished
          </p>
          <p className="mt-2 font-body text-[15px] leading-[1.6] text-void">
            This API requires a sign-in but is missing part of its setup. Set{' '}
            <code className="font-label text-[13px] text-ink">ZITADEL_URL</code>,{' '}
            <code className="font-label text-[13px] text-ink">ZITADEL_TOKEN</code> and{' '}
            <code className="font-label text-[13px] text-ink">AUTH_TOKEN_SECRET</code> in{' '}
            <code className="font-label text-[13px] text-ink">backend/.env</code> and restart it.
          </p>
        </div>
      </div>
    )
  }

  if (state === 'gated' && !user) {
    // The marketing story now owns the public empty-hash root. Once somebody
    // enters `#/`, the task is focused: authenticate, then continue into the
    // studio. Keeping the card here also prevents the public landing page from
    // recursively appearing inside its own call to action.
    return (
      <main className="flex min-h-dvh items-center justify-center bg-canvas px-6 py-8 font-body text-body">
        <AuthScreen />
      </main>
    )
  }

  return children
}
