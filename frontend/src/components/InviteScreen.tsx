import { useEffect, useState } from 'react'
import type { User } from '@supabase/supabase-js'
import { acceptInvite, fetchInvite, setCurrentWorkspace } from '../lib/api'
import { currentUser, onSession, signOut } from '../lib/auth'
import { ACTION, ACTION_PRIMARY, DISPLAY, MONO_LABEL } from './tokens'
import type { InvitePreview } from '../types'

/**
 * "Ana invited you to Northline Studio."
 *
 * The invitation is readable before signing in, because deciding whether to
 * make an account is a decision somebody should be able to make informed. What
 * it cannot do before signing in is be accepted: joining a team means being
 * somebody, and the server checks that the person accepting is the person the
 * invitation was sent to.
 */

/**
 * An address, enough to recognise and not enough to harvest.
 *
 * Anyone holding the link can open this page - that is what makes an invitation
 * work - so printing the whole address would publish it to whoever the link was
 * forwarded to. The first letter and the domain are enough for the person it
 * belongs to to know it is theirs.
 */
function masked(email: string): string {
  const [name, domain] = String(email || '').split('@')
  if (!domain) return email || ''
  const head = name.slice(0, 1)
  return `${head}${'\u2022'.repeat(Math.max(3, Math.min(name.length - 1, 6)))}@${domain}`
}

type InviteScreenProps = {
  token: string
}

export default function InviteScreen({ token }: InviteScreenProps) {
  const [invite, setInvite] = useState<InvitePreview | null>(null)
  const [user, setUser] = useState<User | null>(() => currentUser())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    fetchInvite(token)
      .then((found) => live && setInvite(found))
      .catch((failure) => live && setError(failure?.message || 'That invitation could not be read.'))
    return onSession((session) => live && setUser(session?.user || null))
  }, [token])

  const join = () => {
    setBusy(true)
    setError('')
    acceptInvite(token)
      .then((team) => {
        setCurrentWorkspace(team.workspace)
        window.location.hash = '#/'
        window.location.reload()
      })
      .catch((failure) => {
        setError(failure?.message || 'That invitation was not accepted.')
        setBusy(false)
      })
  }

  if (!invite && !error) {
    return <p className={`${MONO_LABEL} px-6 py-16 text-center`}>Reading the invitation</p>
  }

  const usable = invite?.valid && !error
  const mismatched =
    usable && user?.email && invite.email && user.email.toLowerCase() !== invite.email.toLowerCase()

  // Signed in as somebody else. Not a disabled button on an otherwise inviting
  // page - the answer here is no, and the only thing worth offering is the way
  // to become the person it was sent to.
  if (mismatched) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center px-4 py-10">
        {/* p-7 sm:p-8, shared across the single-card family (see AuthGate.tsx). */}
        <div className="w-full max-w-[28rem] rounded-[18px] border border-alert/40 bg-paper p-7 shadow-raised sm:p-8">
          <p className="font-label text-[12px] uppercase tracking-[0.14em] text-alert">
            Not your invitation
          </p>
          <h1 className={`${DISPLAY} mt-3 text-[22px] leading-[1.2] text-ink`}>
            This one was sent to {masked(invite.email)}
          </h1>
          <p className="mt-3 font-body text-[14.5px] leading-[1.6] text-void">
            You are signed in as <span className="text-ink">{user.email}</span>. An invitation
            belongs to the address it was sent to, so this one cannot be accepted from here.
          </p>

          <button
            type="button"
            onClick={() => signOut().then(() => window.location.reload())}
            className={`${ACTION_PRIMARY} mt-5 w-full`}
          >
            Sign out and use {masked(invite.email)}
          </button>
          <a href="#/" className={`${ACTION} mt-3 block text-center`}>
            Stay signed in as {user.email}
          </a>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-4 py-10">
      {/* p-7 sm:p-8, shared across the single-card family (see AuthGate.tsx). */}
      <div className="w-full max-w-[28rem] rounded-[18px] border border-rule bg-paper p-7 shadow-raised sm:p-8">
        <p className={MONO_LABEL}>Invitation</p>
        <h1 className={`${DISPLAY} mt-3 text-[24px] leading-[1.2] text-ink`}>
          {usable
            ? `${invite.invited_by || 'Somebody'} invited you to ${invite.name}`
            : 'This invitation cannot be used'}
        </h1>

        {usable ? (
          <>
            <p className="mt-3 font-body text-[14.5px] leading-[1.6] text-void">
              You will join as {invite.role === 'admin' ? 'an admin' : 'a member'} —{' '}
              {invite.role === 'admin'
                ? 'you can change anything in this workspace, including its settings.'
                : 'you can prepare quotations and proposals, but not change the studio’s settings or delete anything.'}
            </p>

            <div className="mt-5 rounded-[13px] border border-rule bg-duplicate px-4 py-3">
              <p className={MONO_LABEL}>Sent to</p>
              <p className="mt-1 font-body text-[14px] text-ink">{masked(invite.email)}</p>
            </div>

            <button
              type="button"
              onClick={join}
              disabled={busy}
              className={`${ACTION_PRIMARY} mt-5 w-full`}
            >
              {busy ? 'Joining' : 'Join the workspace'}
            </button>
            <a href="#/" className={`${ACTION} mt-3 block text-center`}>
              No thanks
            </a>
          </>
        ) : (
          <>
            <p className="mt-3 font-body text-[14.5px] leading-[1.6] text-void">
              {error || invite?.problem || 'It may have been used already, or withdrawn.'}
            </p>
            <a href="#/" className={`${ACTION} mt-5 block text-center`}>
              Go to PRISM
            </a>
          </>
        )}
      </div>
    </div>
  )
}
