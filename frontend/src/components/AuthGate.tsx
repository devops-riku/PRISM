import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { User } from '@supabase/supabase-js'
import { currentUser, onSession, signInRequired, startSession, supabase } from '../lib/auth'
import LandingScreen from './LandingScreen'

/**
 * Nothing is shown until the question "who is this?" has an answer — on the
 * installs that ask it.
 *
 * An install with no Supabase project configured has no accounts, the API
 * answers everyone, and this renders the app untouched. Putting a sign-in
 * screen in front of an API that would answer a stranger anyway is theatre, and
 * the one thing an auth screen must never be is decorative.
 *
 * Where accounts do exist, the gate holds until Supabase has restored whatever
 * session the browser was keeping. That wait is deliberately silent for the
 * first moment: a sign-in card that flashes up and vanishes because a session
 * was there all along reads as having been signed out.
 */

/** The four answers to "who is this?", including not knowing yet. */
type GateState = 'reading' | 'open' | 'misconfigured' | 'gated'

type AuthGateProps = { children: ReactNode }

export default function AuthGate({ children }: AuthGateProps) {
  const [state, setState] = useState<GateState>('reading')
  const [user, setUser] = useState<User | null>(null)

  useEffect(() => {
    let live = true

    signInRequired()
      .then(async (required) => {
        if (!live) return
        if (!required) {
          setState('open')
          return
        }
        const client = await supabase()
        if (!live) return
        if (!client) {
          // The server says sign-in is required but gave no project to sign in
          // to. Saying so beats an endless spinner.
          setState('misconfigured')
          return
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
            This API requires a sign-in but did not name an account service to sign in to. Set{' '}
            <code className="font-label text-[13px] text-ink">SUPABASE_URL</code> and{' '}
            <code className="font-label text-[13px] text-ink">SUPABASE_ANON_KEY</code> in{' '}
            <code className="font-label text-[13px] text-ink">backend/.env</code> and restart it.
          </p>
        </div>
      </div>
    )
  }

  if (state === 'gated' && !user) {
    // `LandingScreen`, not a bare `AuthScreen` on an empty canvas. This is
    // the first thing anybody ever sees of PRISM, and a lone sign-in card
    // says "you are locked out" where the only useful thing to say is what
    // the product does. The card is still there, on one side of a page that
    // answers that first; it owns its own pinning and scrolling, so this
    // branch hands over the whole viewport rather than centring anything.
    return <LandingScreen />
  }

  return children
}
