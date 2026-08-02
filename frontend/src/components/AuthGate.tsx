import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { User } from '@supabase/supabase-js'
import { currentUser, onSession, signInRequired, startSession, supabase } from '../lib/auth'
import AuthScreen from './AuthScreen'

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
        <div className="max-w-[34rem] rounded-[18px] border border-rule bg-paper p-7 shadow-raised">
          <p className="font-label text-[12px] uppercase tracking-[0.14em] text-alert">
            Sign-in not finished
          </p>
          <p className="mt-2 font-body text-[15px] leading-[1.6] text-void">
            This API requires a sign-in but did not name a Supabase project to sign in to. Set{' '}
            <code className="font-label text-[13px] text-ink">SUPABASE_URL</code> and{' '}
            <code className="font-label text-[13px] text-ink">SUPABASE_ANON_KEY</code> in{' '}
            <code className="font-label text-[13px] text-ink">backend/.env</code> and restart it.
          </p>
        </div>
      </div>
    )
  }

  if (state === 'gated' && !user) {
    return (
      // Pinned to the viewport rather than stretched by it: signing in is one
      // screen's worth of decision, and a page that scrolls before you have
      // done it reads as a form.
      <div className="grid h-dvh place-items-center overflow-hidden bg-canvas px-5 py-5 font-body text-body">
        <AuthScreen />
      </div>
    )
  }

  return children
}
