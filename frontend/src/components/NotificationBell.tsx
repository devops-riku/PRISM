import { Popover, PopoverButton, PopoverPanel } from '@headlessui/react'
import { useEffect, useRef, useState } from 'react'
import { clearSeen, markRead, useNotifications } from '../lib/notifications'
import { formatDate } from '../lib/format'
import { MONO_LABEL } from './tokens'

/**
 * What happened while you were not looking.
 *
 * The count is the whole feature: a bell with nothing on it should be silent
 * furniture, and one with a number on it should be worth opening. So the server
 * only ever writes notes somebody has to act on or would want to know — work
 * that landed, work that failed, and changes to the team or to what the studio
 * charges. Progress, saves and "you did this a second ago" are deliberately not
 * here.
 *
 * Read is marked when the panel CLOSES, not when it opens, so the rows you came
 * to look at are still marked new while you are looking at them.
 */

const TONE: Record<string, string> = {
  quotation_failed: 'alert',
  quotation_rejected: 'alert',
  quotation_crashed: 'alert',
  revision_failed: 'alert',
  proposal_failed: 'alert',
  generation_blocked: 'alert',
  work_lost_to_restart: 'alert',
  removed_from_team: 'alert',
}

type DotProps = {
  kind: string
  unread: boolean
}

function Dot({ kind, unread }: DotProps) {
  const alert = TONE[kind] === 'alert'
  return (
    <span
      aria-hidden="true"
      className={`mt-[7px] h-1.5 w-1.5 flex-none rounded-full ${
        alert ? 'bg-alert' : unread ? 'bg-ballpoint' : 'bg-rule'
      }`}
    />
  )
}

export default function NotificationBell() {
  const { unread, notes } = useNotifications()
  const [open, setOpen] = useState(false)
  const wasOpen = useRef(false)

  // Marked read in an effect, not during render: render runs for reasons that
  // have nothing to do with this panel - a poll landing, a parent updating -
  // and a write fired from render would go off at all of them.
  useEffect(() => {
    if (wasOpen.current && !open) markRead()
    wasOpen.current = open
  }, [open])

  return (
    <Popover className="relative">
      {({ open: isOpen, close }) => {
        if (isOpen !== open) setOpen(isOpen)

        return (
          <>
            <PopoverButton
              aria-label={
                unread ? `Notifications, ${unread} unread` : 'Notifications, nothing new'
              }
              className="relative flex h-9 w-9 items-center justify-center rounded-md border border-transparent text-void transition-[background-color,border-color,color] duration-150 hover:border-rule hover:bg-paper hover:text-ballpoint data-[open]:border-rule data-[open]:bg-paper"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-[18px] w-[18px]"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5Z" />
                <path d="M10.2 18.5a2 2 0 0 0 3.6 0" />
              </svg>
              {unread ? (
                /* Outside the icon, not over it: a count sitting on the bell
                   hid the thing it was counting. The canvas-coloured ring is
                   what keeps it legible against the icon's strokes. */
                <span className="pointer-events-none absolute -right-[3px] -top-[3px] flex h-[16px] min-w-[16px] items-center justify-center rounded-pill bg-ballpoint px-[4px] font-label text-[10px] font-medium leading-none text-paper ring-2 ring-canvas">
                  {unread > 9 ? '9+' : unread}
                </span>
              ) : null}
            </PopoverButton>

            {/* Neither modal nor transitioned, for the reason written out in
                RowMenu: one pads the document and shifts the page, the other
                shows the panel's pre-measurement position. */}
            <PopoverPanel
              anchor="bottom end"
              modal={false}
              className="z-50 w-[22rem] rounded-[18px] border border-rule bg-paper shadow-raised [--anchor-gap:8px] focus:outline-none"
            >
              <div className="flex items-baseline justify-between gap-4 border-b border-hairline px-4 py-3">
                <p className={MONO_LABEL}>Notifications</p>
                {notes.length ? (
                  <button
                    type="button"
                    onClick={() => clearSeen()}
                    className="font-body text-[12.5px] text-void hover:text-ballpoint"
                  >
                    Clear read
                  </button>
                ) : null}
              </div>

              {notes.length === 0 ? (
                <p className="px-4 py-8 text-center font-body text-[13.5px] leading-[1.6] text-void">
                  Nothing yet. When a quotation lands, a proposal is built, or somebody joins the
                  team, it turns up here.
                </p>
              ) : (
                <div className="no-scrollbar max-h-[24rem] overflow-y-auto">
                  {notes.map((note) => {
                    const body = (
                      <>
                        <Dot kind={note.kind} unread={!note.read_at} />
                        <span className="min-w-0 flex-1">
                          <span
                            className={`block font-body text-[14px] leading-[1.4] ${
                              note.read_at ? 'text-void' : 'text-ink'
                            }`}
                          >
                            {note.title}
                          </span>
                          {note.body ? (
                            <span className="mt-0.5 block font-body text-[12.5px] leading-[1.5] text-void">
                              {note.body}
                            </span>
                          ) : null}
                          <span className="mt-1 block font-label text-[11px] uppercase tracking-[0.1em] text-faint">
                            {formatDate(note.at, { withTime: true })}
                          </span>
                        </span>
                      </>
                    )

                    return note.href ? (
                      <a
                        key={note.id}
                        href={note.href}
                        // Closing on the way out does two things: it marks the
                        // panel read, and it stops the panel hanging over the
                        // page it just sent you to.
                        onClick={() => close()}
                        className="row-touch flex gap-3 border-b border-hairline px-4 py-3 no-underline last:border-b-0 hover:bg-duplicate"
                      >
                        {body}
                      </a>
                    ) : (
                      <div
                        key={note.id}
                        className="flex gap-3 border-b border-hairline px-4 py-3 last:border-b-0"
                      >
                        {body}
                      </div>
                    )
                  })}
                </div>
              )}

              <div className="border-t border-hairline px-4 py-2">
                <a
                  href="#/jobs"
                  className="font-body text-[12.5px] text-ballpoint no-underline hover:underline"
                >
                  Everything in progress
                </a>
              </div>
            </PopoverPanel>
          </>
        )
      }}
    </Popover>
  )
}
