import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import {
  claimWorkspace,
  fetchTeam,
  inviteToTeam,
  removeMember,
  revokeInvite,
  setMemberRole,
} from '../lib/api'
import Dropdown from './Dropdown'
import RowMenu from './RowMenu'
import { ACTION, ACTION_PRIMARY, DISPLAY, MONO_LABEL, WELL } from './tokens'
import type { MemberRole, Team } from '../types'

/**
 * The people on this workspace, and the way to add one.
 *
 * Two roles, and the page says what the difference is rather than making
 * somebody find out by being refused: an admin sees every screen and can delete
 * things; a member prepares quotations and proposals and cannot reach Settings.
 *
 * An invitation is a link with a token. The email is only how it travels — when
 * Resend is not configured, or refuses, the link comes back anyway and can be
 * sent by hand. An invite lost to a mail problem would be the worst of both.
 */

const ROLES: { value: MemberRole; label: string; hint: string }[] = [
  { value: 'admin', label: 'Admin', hint: 'Every screen, including Settings' },
  { value: 'member', label: 'Member', hint: 'No Settings, no deleting' },
]

function initials(email: string): string {
  const name = String(email || '').split('@')[0] || '?'
  const parts = name.split(/[.\-_]/).filter(Boolean)
  if (parts.length > 1) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

export default function TeamPanel() {
  const [team, setTeam] = useState<Team | null>(null)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<MemberRole>('member')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [copied, setCopied] = useState('')

  const load = () =>
    fetchTeam()
      .then((found) => {
        setTeam(found)
        setError('')
      })
      .catch((failure) => setError(failure?.message || 'The team did not load.'))

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const admin = team?.your_role === 'admin'
  // A workspace made before teams existed has no roster. It stays open to
  // everybody signed in - exactly as it was - until somebody deliberately
  // takes charge of it. Claiming used to happen silently on first sight, which
  // handed a studio's book to whoever opened it first.
  const unclaimed = Boolean(team && team.members.length === 0)

  const send = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!email.trim() || busy) return
    setBusy(true)
    setError('')
    setNotice('')
    inviteToTeam(email.trim(), role)
      .then((made) => {
        setEmail('')
        setNotice(
          made.emailed
            ? `Invitation emailed to ${made.email}.`
            : `Invitation ready for ${made.email}. ${made.problem} Copy the link below.`,
        )
        return load()
      })
      .catch((failure) => setError(failure?.message || 'That invitation was not created.'))
      .finally(() => setBusy(false))
  }

  const copy = (link: string) => {
    navigator.clipboard
      ?.writeText(link)
      .then(() => {
        setCopied(link)
        window.setTimeout(() => setCopied(''), 1500)
      })
      .catch(() => setError('That link could not be copied.'))
  }

  if (!team) {
    return (
      <p className={`${MONO_LABEL} px-1 py-6`}>{error || 'Reading the team'}</p>
    )
  }

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h3 className={`${DISPLAY} text-[17px]`}>People on {team.name}</h3>
        <p className={MONO_LABEL}>
          {team.members.length} {team.members.length === 1 ? 'person' : 'people'}
          {team.invites.length ? ` · ${team.invites.length} invited` : ''}
        </p>
      </div>

      <p className="mt-2 max-w-[72ch] font-body text-[13px] leading-[1.6] text-void">
        An admin sees every screen and can delete quotations, proposals and the workspace itself. A
        member prepares work but cannot open Settings or delete anything.
        {admin ? '' : ' You are a member here, so this list is read-only.'}
      </p>

      {unclaimed ? (
        <div className="mt-4 rounded-xl border border-dashed border-rule px-4 py-4">
          <p className="font-body text-[14px] text-ink">Nobody administers this workspace yet.</p>
          <p className="mt-1 max-w-[60ch] font-body text-[13px] leading-[1.6] text-void">
            Until somebody takes charge, anyone signed in can open it and everyone is treated as an
            admin. Claiming it makes you its admin and everybody else needs an invitation.
          </p>
          <button
            type="button"
            className={`${ACTION_PRIMARY} mt-3`}
            onClick={() =>
              claimWorkspace()
                .then(load)
                .catch((failure) => setError(failure?.message || 'That workspace was not claimed.'))
            }
          >
            Take charge of this workspace
          </button>
        </div>
      ) : null}

      <div className="mt-4 rounded-xl border border-rule">
        {team.members.map((member) => (
          <div
            key={member.email}
            className="row-touch flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-hairline px-4 py-3 last:border-b-0"
          >
            <div className="flex min-w-[15rem] flex-1 items-center gap-3">
              <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[11px] bg-accent-soft font-label text-[12px] font-medium text-ballpoint">
                {initials(member.email)}
              </span>
              <div className="min-w-0">
                <p className="truncate font-body text-[14px] text-ink">
                  {member.email}
                  {member.you ? <span className="text-faint"> · you</span> : null}
                </p>
                {member.added_at ? (
                  <p className={`${MONO_LABEL} mt-0.5`}>
                    Joined {member.added_at.slice(0, 10)}
                  </p>
                ) : null}
              </div>
            </div>

            <span className={member.role === 'admin' ? 'chip chip--live' : 'chip'}>
              {member.role}
            </span>

            {admin ? (
              <RowMenu
                label={`Actions for ${member.email}`}
                items={[
                  member.role === 'admin'
                    ? {
                        label: 'Make a member',
                        onSelect: () =>
                          setMemberRole(member.email, 'member')
                            .then(load)
                            .catch((failure) => setError(failure?.message || 'Not changed.')),
                      }
                    : {
                        label: 'Make an admin',
                        onSelect: () =>
                          setMemberRole(member.email, 'admin')
                            .then(load)
                            .catch((failure) => setError(failure?.message || 'Not changed.')),
                      },
                  {
                    label: 'Remove from team',
                    danger: true,
                    onSelect: () =>
                      removeMember(member.email)
                        .then(load)
                        .catch((failure) => setError(failure?.message || 'Not removed.')),
                  },
                ]}
              />
            ) : null}
          </div>
        ))}
      </div>

      {team.invites.length ? (
        <div className="mt-5">
          <p className={MONO_LABEL}>Invited, not yet joined</p>
          <div className="mt-2 rounded-xl border border-dashed border-rule">
            {team.invites.map((entry) => (
              <div
                key={entry.link}
                className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2 border-b border-hairline px-4 py-2.5 last:border-b-0"
              >
                <div className="min-w-[14rem] flex-1">
                  <p className="truncate font-body text-[14px] text-ink">{entry.email}</p>
                  <p className={`${MONO_LABEL} mt-0.5`}>
                    {entry.role} · expires {entry.expires_at.slice(0, 10)}
                  </p>
                </div>
                <button type="button" className={ACTION} onClick={() => copy(entry.link)}>
                  {copied === entry.link ? 'Copied' : 'Copy link'}
                </button>
                {admin ? (
                  <button
                    type="button"
                    className={`${ACTION} border-alert text-alert`}
                    onClick={() => {
                      // The token is the last segment of the link, and `.pop()`
                      // is `string | undefined` - on a link with no segments it
                      // is the empty string, never `undefined`, so a `??` would
                      // never fire and the DELETE would go out against no
                      // invitation at all. There is nothing to withdraw here;
                      // say so rather than ask the server about nothing.
                      const token = entry.link.split('/').pop()
                      if (!token) {
                        setError('Not withdrawn.')
                        return
                      }
                      revokeInvite(token)
                        .then(load)
                        .catch((failure) => setError(failure?.message || 'Not withdrawn.'))
                    }}
                  >
                    Withdraw
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {admin ? (
        <form onSubmit={send} className="mt-5 flex flex-wrap items-end gap-3">
          <div className="min-w-[16rem] flex-1">
            <label htmlFor="invite_email" className={MONO_LABEL}>
              Invite someone
            </label>
            <input
              id="invite_email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="them@studio.com"
              className={`${WELL} mt-2`}
            />
          </div>
          <div className="w-[190px]">
            <label htmlFor="invite_role" className={MONO_LABEL}>
              As
            </label>
            <Dropdown
              id="invite_role"
              className="mt-2"
              value={role}
              onChange={(next: string) => setRole(next === 'admin' ? 'admin' : 'member')}
              options={ROLES}
            />
          </div>
          <button type="submit" disabled={busy || !email.trim()} className={ACTION_PRIMARY}>
            {busy ? 'Sending' : 'Send invitation'}
          </button>
        </form>
      ) : null}

      {!team.email_configured && admin ? (
        <p className="mt-3 font-body text-[13px] leading-[1.6] text-faint">
          No email is configured, so invitations are created and shown here as links to send
          yourself. Set RESEND_API_KEY and RESEND_FROM in backend/.env to have them delivered.
        </p>
      ) : null}

      {notice ? <p className="mt-3 font-body text-[13.5px] text-void">{notice}</p> : null}
      {error ? (
        <p role="alert" className="mt-3 font-body text-[13.5px] text-alert">
          {error}
        </p>
      ) : null}
    </div>
  )
}
