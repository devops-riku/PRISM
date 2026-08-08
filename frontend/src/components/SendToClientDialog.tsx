import { Dialog, DialogPanel, DialogTitle } from '@headlessui/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchHealth, fetchIntakeLink, fetchSettings } from '../lib/api'
import type { QuotationDraft } from '../lib/api'
import type { Intake } from '../types'

/**
 * The compose window behind "Send to client".
 *
 * A quotation leaves this studio as an email somebody wrote, not as a state
 * change with a template attached. So this is shaped like the thing it is:
 * To, Subject, a body, and the words already in it. What is on this screen is
 * exactly what the client receives - the draft is composed here and passed to
 * the server, rather than composed on the server and described here, precisely
 * so the two can never drift.
 *
 * NOTHING IS SAVED. Close the window and the draft is gone. That is a real
 * cost - a studio who writes three paragraphs, closes to go and check
 * something, and comes back to a fresh draft will be annoyed, and rightly. It
 * is chosen anyway because the alternative is a half-written email living on
 * the intake record, needing a field, a migration, and an answer to "which
 * draft wins when two people have the row open". Prefilled and disposable is
 * the honest version of what this actually is: a send button with the words
 * shown first.
 *
 * WHY THE LINK IS FETCHED HERE rather than read off the row: the queue
 * deliberately carries no tokens (`Intake.token` is `exclude=True` server-side)
 * so that a screenshot, an export, or a member reading the list discloses
 * nothing. This makes the same single-purpose admin call `Copy link` makes, one
 * intake at a time, on purpose.
 */

type SendToClientDialogProps = {
  row: Intake
  busy: boolean
  /** The server's answer, or null while it is still being asked. */
  error: string
  onSend: (bundleId: string, draft: QuotationDraft) => void
  onClose: () => void
}

/** A person's name out of an address, when that is all there is to go on. */
function greetingFor(email: string): string {
  const local = (email || '').split('@')[0]?.trim() || ''
  if (!local) return 'Hello,'
  // `maria.santos` and `maria_santos` are one person written two ways.
  const first = local.split(/[._-]/)[0] || local
  if (!/^[a-z]+$/i.test(first)) return 'Hello,'
  return `Hi ${first[0].toUpperCase()}${first.slice(1).toLowerCase()},`
}

function draftFor(row: Intake, studio: string, link: string): { subject: string; message: string } {
  const project = String((row.preset as Record<string, unknown>)?.project_name || '').trim()
  const house = studio.trim() || 'our studio'
  return {
    subject: project ? `Quotation for ${project}` : `Your quotation from ${house}`,
    message: [
      greetingFor(row.client_email),
      '',
      'Your quotation is ready. You can read it here:',
      '',
      link || '…',
      '',
      'The link is yours alone - please do not forward it.',
      '',
      'Thank you,',
      house,
    ].join('\n'),
  }
}

const FIELD =
  'w-full rounded-[11px] border border-[var(--well-border)] bg-duplicate px-3 py-2 font-body ' +
  'text-[14px] leading-[1.55] text-ink shadow-[var(--shadow-inset)] outline-none ' +
  'placeholder:text-faint focus-visible:shadow-[var(--shadow-ring)]'

const LABEL =
  'block font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void'

export default function SendToClientDialog({
  row,
  busy,
  error,
  onSend,
  onClose,
}: SendToClientDialogProps) {
  const [link, setLink] = useState('')
  const [linkError, setLinkError] = useState('')
  const [mailReady, setMailReady] = useState<boolean | null>(null)
  const [studioName, setStudioName] = useState('')
  const [bundleId, setBundleId] = useState(row.bundle_ids[0] || '')
  const [subject, setSubject] = useState('')
  const [message, setMessage] = useState('')
  const [copyNote, setCopyNote] = useState('')

  // True once the studio has typed into either field. The draft is rebuilt when
  // the link arrives, and rebuilding it under somebody who has started writing
  // would delete their work - so the rebuild stops the moment they touch it.
  const edited = useRef(false)

  useEffect(() => {
    let live = true
    fetchIntakeLink(row.id)
      .then((value) => {
        if (!live) return
        setLink(value)
      })
      .catch((problem: unknown) => {
        if (!live) return
        setLinkError(
          problem instanceof Error
            ? problem.message
            : 'That link did not come back. Try again, or reissue it.',
        )
      })
    fetchHealth()
      .then((health) => live && setMailReady(health.mail_configured))
      // A server that will not answer /api/health is not a reason to block a
      // send. Treated as "no mail", which degrades to the copy-out flow this
      // app had before it could send anything at all.
      .catch(() => live && setMailReady(false))
    fetchSettings()
      .then((defaults) => live && setStudioName(defaults.studio_name || ''))
      // The sign-off falls back to "our studio". Not worth an error banner over
      // a draft the studio is about to read and can edit.
      .catch(() => {})
    return () => {
      live = false
    }
  }, [row.id])

  useEffect(() => {
    if (edited.current) return
    const next = draftFor(row, studioName, link)
    setSubject(next.subject)
    setMessage(next.message)
  }, [row, studioName, link])

  const plain = useMemo(() => `Subject: ${subject}\n\n${message}`, [subject, message])

  const copy = () => {
    const board = navigator.clipboard
    if (!board) {
      // Absent, not merely refused, over plain HTTP on anything but localhost -
      // the same trap `handleCopyLink` documents. Saying "copied" here would be
      // a lie a studio only discovers when they paste nothing into an email.
      setCopyNote('This browser will not let a page copy for you. Select the text and copy it.')
      return
    }
    board
      .writeText(plain)
      .then(() => setCopyNote('Copied. Paste it into your own email.'))
      .catch(() => setCopyNote('The copy did not go through. Select the text and copy it.'))
  }

  const canSend = mailReady === true && Boolean(bundleId) && Boolean(row.client_email)

  return (
    <Dialog open onClose={busy ? () => {} : onClose} className="relative z-[60]">
      <div className="fixed inset-0 bg-ink/25 backdrop-blur-[2px]" aria-hidden="true" />
      <div className="fixed inset-0 flex items-start justify-center overflow-y-auto px-4 py-[8vh]">
        <DialogPanel className="w-full max-w-[38rem] overflow-hidden rounded-[18px] border border-rule bg-paper shadow-raised">
          <div className="border-b border-hairline px-5 py-4">
            <DialogTitle className="font-label text-[13px] font-medium uppercase tracking-[0.14em] text-void">
              Send to client
            </DialogTitle>
          </div>

          <div className="flex flex-col gap-4 px-5 py-5">
            {/* Read-only, and not a disabled input: the address is the intake's
                own record, and a field that looks editable but is not is worse
                than one that plainly is not. Changing who this goes to means
                changing the request. */}
            <div>
              <span className={LABEL}>To</span>
              <p className="mt-2 font-body text-[14px] leading-[1.55] text-ink">
                {row.client_email || (
                  <span className="text-faint">
                    This request has no client address - copy the draft and send it yourself.
                  </span>
                )}
              </p>
            </div>

            {row.bundle_ids.length > 1 ? (
              <div>
                <label className={LABEL} htmlFor="send-bundle">
                  Which quotation
                </label>
                <p className="mb-2 mt-1 max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                  This request has {row.bundle_ids.length} on file. They are numbered because
                  nothing on the record names them - open one in a new tab to see which is which.
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    id="send-bundle"
                    className={FIELD + ' max-w-[16rem]'}
                    value={bundleId}
                    onChange={(event) => setBundleId(event.target.value)}
                  >
                    {row.bundle_ids.map((id, index) => (
                      <option key={id} value={id}>
                        Quotation {index + 1}
                      </option>
                    ))}
                  </select>
                  <a
                    href={`#/q/${bundleId}`}
                    target="_blank"
                    rel="noopener"
                    className="font-body text-[13px] text-ballpoint underline underline-offset-2"
                  >
                    Read it first
                  </a>
                </div>
              </div>
            ) : null}

            <div>
              <label className={LABEL} htmlFor="send-subject">
                Subject
              </label>
              <input
                id="send-subject"
                className={FIELD + ' mt-2'}
                value={subject}
                onChange={(event) => {
                  edited.current = true
                  setSubject(event.target.value)
                }}
              />
            </div>

            <div>
              <label className={LABEL} htmlFor="send-message">
                Message
              </label>
              <textarea
                id="send-message"
                rows={11}
                className={FIELD + ' mt-2 resize-y'}
                value={message}
                onChange={(event) => {
                  edited.current = true
                  setMessage(event.target.value)
                }}
              />
            </div>

            {linkError ? (
              <p role="alert" className="font-body text-[13px] leading-[1.6] text-alert">
                {linkError}
              </p>
            ) : null}

            {mailReady === false ? (
              <p className="max-w-[62ch] font-body text-[13px] leading-[1.6] text-void">
                This server has no email configured, so PRISM cannot send this for you. Copy the
                draft and send it from your own address - then mark it sent.
              </p>
            ) : null}

            {error ? (
              <p role="alert" className="max-w-[62ch] font-body text-[13px] leading-[1.6] text-alert">
                {error}
              </p>
            ) : null}

            {copyNote ? (
              <p role="status" className="font-body text-[13px] leading-[1.6] text-void">
                {copyNote}
              </p>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t border-hairline px-5 py-4">
            {canSend ? (
              <button
                type="button"
                disabled={busy}
                className="rounded-full bg-ballpoint px-4 py-2 font-label text-[13px] font-medium text-paper disabled:opacity-60"
                onClick={() => onSend(bundleId, { subject, message, notify: true })}
              >
                {busy ? 'Sending' : 'Send email'}
              </button>
            ) : null}

            <button
              type="button"
              className="rounded-full border border-rule px-4 py-2 font-label text-[13px] font-medium text-ink"
              onClick={copy}
            >
              Copy
            </button>

            {/* Offered when PRISM cannot send, and this is the one button that
                asserts something it cannot verify: pressing it says "I sent
                this", and the queue believes you. Kept deliberately plain and
                second, so it is not the thing a hurried studio hits by default
                on a server that could have sent the mail itself. */}
            {mailReady === false ? (
              <button
                type="button"
                disabled={busy || !bundleId}
                className="rounded-full border border-rule px-4 py-2 font-label text-[13px] font-medium text-ink disabled:opacity-60"
                onClick={() => onSend(bundleId, { subject, message, notify: false })}
              >
                {busy ? 'Marking' : 'Mark as sent'}
              </button>
            ) : null}

            <button
              type="button"
              disabled={busy}
              className="ml-auto font-label text-[13px] font-medium text-void disabled:opacity-60"
              onClick={onClose}
            >
              Not yet
            </button>
          </div>
        </DialogPanel>
      </div>
    </Dialog>
  )
}
