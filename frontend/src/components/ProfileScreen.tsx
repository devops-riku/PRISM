import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { User } from '@supabase/supabase-js'
import { currentUser, onSession, signInRequired, signOut } from '../lib/auth'
import { fetchSettings } from '../lib/api'
import { initialsFor } from './AppHeader'
import { ACTION, DISPLAY, MONO_LABEL } from './tokens'

/**
 * You, as this install knows you.
 *
 * Deliberately thin. PRISM keeps nothing about a person: the account lives in
 * Supabase, and what a studio charges lives in Settings. So this page states
 * what is true — which account is signed in, how it signed in, and when — and
 * points at the two screens that own everything else. A profile page that
 * invents fields to look substantial is a form nobody asked to fill in.
 *
 * On an install with no accounts it says that plainly rather than showing an
 * empty card. Nothing is signed in because nothing has to be.
 */

type RowProps = {
  label: string
  children: ReactNode
}

function Row({ label, children }: RowProps) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-hairline py-3 last:border-b-0">
      <span className={MONO_LABEL}>{label}</span>
      <span className="font-body text-[14px] text-ink">{children}</span>
    </div>
  )
}

export default function ProfileScreen() {
  const [user, setUser] = useState<User | null>(() => currentUser())
  const [required, setRequired] = useState<boolean | null>(null)
  const [studio, setStudio] = useState('')

  useEffect(() => {
    let live = true
    signInRequired()
      .then((yes) => live && setRequired(yes))
      .catch(() => live && setRequired(false))
    fetchSettings()
      .then((saved) => live && setStudio(saved.studio_name || ''))
      .catch(() => {})
    const stop = onSession((session) => live && setUser(session?.user || null))
    return () => {
      live = false
      stop()
    }
  }, [])

  const email = user?.email || ''
  const provider = user?.app_metadata?.provider || ''
  const lastSeen = user?.last_sign_in_at
    ? new Date(user.last_sign_in_at).toLocaleString()
    : ''

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <section className="flex min-h-0 flex-1 flex-col rounded-xl border border-rule bg-paper shadow-sheet">
        <div className="shrink-0 border-b border-rule px-5 py-3 sm:px-6">
          <h2 className={`${DISPLAY} text-[20px]`}>Profile</h2>
          <p className="mt-2 max-w-[70ch] font-body text-[13px] leading-[1.6] text-void">
            The account you are signed in with. What the studio charges, and what its documents say,
            live in Settings — they belong to the workspace rather than to you.
          </p>
        </div>

        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
          <div className="max-w-[46rem]">
            <div className="flex items-center gap-4">
              <span className="flex h-14 w-14 items-center justify-center rounded-[18px] bg-ballpoint font-label text-[18px] font-medium tracking-[0.04em] text-paper">
                {initialsFor(email || studio || 'PRISM')}
              </span>
              <div className="min-w-0">
                <p className="truncate font-body text-[16px] text-ink">
                  {email || (required === false ? 'No account needed here' : 'Not signed in')}
                </p>
                <p className={`${MONO_LABEL} mt-1`}>{studio || 'PRISM'}</p>
              </div>
            </div>

            <div className="mt-6 rounded-xl border border-rule bg-paper px-5 py-1">
              {email ? (
                <>
                  <Row label="Email">{email}</Row>
                  <Row label="Signed in with">{provider || 'email'}</Row>
                  {lastSeen ? <Row label="Last sign-in">{lastSeen}</Row> : null}
                  <Row label="Account id">
                    {/* `email` is `user?.email || ''`, so this branch cannot be
                        reached with no user - but a string alias carries no
                        narrowing, and `?.` is the honest way to say so. */}
                    <span className="font-label text-[12.5px] text-void">{user?.id}</span>
                  </Row>
                </>
              ) : (
                <Row label="Accounts">
                  {required === false
                    ? 'Not configured — this install has no sign-in, and the API answers anyone who can reach it.'
                    : 'No session.'}
                </Row>
              )}
            </div>

            <div className="mt-6 flex flex-wrap gap-3">
              <a href="#/settings" className={ACTION}>
                Studio settings
              </a>
              <a href="#/teams" className={ACTION}>
                Teams
              </a>
              <a href="#/workspaces" className={ACTION}>
                Workspaces
              </a>
              {email ? (
                <button
                  type="button"
                  className={`${ACTION} border-alert text-alert`}
                  onClick={() => signOut().then(() => window.location.reload())}
                >
                  Sign out
                </button>
              ) : null}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
