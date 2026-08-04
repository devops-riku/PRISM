import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react'
import { useEffect, useState } from 'react'
import { currentUser, onSession, signOut } from '../lib/auth'
import { goBack } from '../lib/navigation'
import CommandBar from './CommandBar'
import NotificationBell from './NotificationBell'
import WorkspaceMenu from './WorkspaceMenu'
import { DISPLAY } from './tokens'

/**
 * The one line of chrome above every screen.
 *
 * Four things, left to right: which book of work is open, whose quotations
 * these are, which screen you are on, and the way out of it. The workspace
 * comes first because it is the widest fact on the page - it decides what every
 * figure below it belongs to. The profile is the studio rather than a person —
 * PRISM has no accounts, and inventing a signed-in user to hang an avatar on
 * would be a lie told in a corner of the interface. What the initials stand for
 * is the name in Settings, so the header says something true and changes when
 * that changes.
 *
 * The menu behind the initials is about you: your profile, the teams you can
 * open, and the way out. Getting to a screen is the command bar's job - one
 * field that reaches everything, which is why there is no nav strip and no
 * list of destinations hiding under an avatar.
 */

//: What this menu is for, now that the command bar reaches every screen.
//: Three items about the person using the app rather than seven about the app -
//: a list of destinations under an avatar was a nav bar wearing a disguise.
const PLACES = [
  { href: '#/profile', label: 'Profile' },
  { href: '#/teams', label: 'Teams' },
  { href: '#/workspaces', label: 'Workspaces' },
]

/** Two letters at most: first letters of the first two words, or the first two. */
export function initialsFor(name: string): string {
  const words = String(name || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (!words.length) return 'PR'
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

type AppHeaderProps = {
  screenName?: string
  studioName?: string
  /** A hash href for the close button. Empty renders no button at all. */
  onClose?: string
}

export default function AppHeader({
  screenName = '',
  studioName = '',
  onClose = '',
}: AppHeaderProps) {
  const studio = studioName.trim() || 'PRISM'

  // Who is signed in, where there are accounts at all. On an install without
  // them this stays null and the menu says nothing about sessions - there is
  // nothing to say.
  const [user, setUser] = useState(() => currentUser())
  useEffect(() => onSession((session) => setUser(session?.user || null)), [])
  const email = user?.email || ''

  return (
    <header className="mx-auto mb-4 flex w-full max-w-sheet shrink-0 items-center justify-between gap-4 border-b border-hairline bg-paper/60 pb-6 sm:pb-8">
      <div className="flex items-center gap-3">
        <a
          href="#/"
          className={`${DISPLAY} text-[15px] tracking-[-0.01em] text-ink no-underline hover:text-ballpoint`}
        >
          PRISM
        </a>
        <WorkspaceMenu />
        {screenName ? (
          <>
            <span aria-hidden="true" className="font-label text-[12px] text-faint">
              /
            </span>
            <p className="font-label text-[12px] uppercase tracking-[0.14em] text-faint">
              {screenName}
            </p>
          </>
        ) : null}
      </div>

      <div className="flex items-center gap-2">
        {/* One field that reaches everything. It sits beside the profile rather
            than in the middle: it is a way through the app, not the subject of
            the page. */}
        <CommandBar />
        {/* The bell shows everywhere, including the front page: it is the one
            piece of chrome that is about you rather than about the screen. */}
        <NotificationBell />
        <Menu>
          <MenuButton
            aria-label={`${studio} — menu`}
            className="flex items-center gap-2 rounded-md border border-transparent py-1 pl-1 pr-2 transition-[background-color,border-color] duration-150 hover:border-rule hover:bg-paper data-[open]:border-rule data-[open]:bg-paper"
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ballpoint font-label text-[11px] font-medium tracking-[0.06em] text-paper">
              {initialsFor(studio)}
            </span>
            <span className="hidden max-w-[16ch] truncate font-body text-[13px] text-body sm:block">
              {studio}
            </span>
            <svg
              aria-hidden="true"
              viewBox="0 0 12 8"
              className="h-2 w-3 flex-none text-faint"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M1 1.5 6 6.5 11 1.5" />
            </svg>
          </MenuButton>

          {/* Neither modal nor transitioned, for the reasons written out in
              RowMenu: one pads the document and slides the page, the other
              makes the panel's pre-measurement position visible. */}
          <MenuItems
            anchor="bottom end"
            modal={false}
            className="z-50 min-w-[13rem] rounded-lg border border-rule bg-paper p-1 shadow-raised [--anchor-gap:6px] focus:outline-none"
          >
            <p className="px-3 pb-1 pt-1.5 font-label text-[12px] uppercase tracking-[0.14em] text-faint">
              {studio}
            </p>
            {email ? (
              <p className="truncate px-3 pb-2 font-body text-[13px] text-void">{email}</p>
            ) : null}
            <div className="mb-1 mt-1 h-px bg-hairline" role="presentation" />
            {PLACES.map((place) => (
              <MenuItem key={place.href}>
                <a
                  href={place.href}
                  className="block rounded-xs px-3 py-2 font-body text-[14px] text-body no-underline data-[focus]:bg-duplicate"
                >
                  {place.label}
                </a>
              </MenuItem>
            ))}

            <div className="my-1 h-px bg-hairline" role="presentation" />
            <MenuItem>
              <button
                type="button"
                disabled={!email}
                title={email ? '' : 'This install has no sign-in configured'}
                onClick={() => signOut().then(() => window.location.reload())}
                className="block w-full rounded-xs px-3 py-2 text-left font-body text-[14px] text-body disabled:cursor-not-allowed disabled:text-faint data-[focus]:bg-duplicate"
              >
                Sign out
              </button>
            </MenuItem>
          </MenuItems>
        </Menu>

        {/* A link, so it can still be middle-clicked and read by a screen
            reader as a destination — but a click goes back to whatever opened
            this screen. The href is the fallback for a page opened directly,
            which is exactly what `goBack` falls back to. */}
        {onClose ? (
          <a
            href={onClose}
            onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
              event.preventDefault()
              goBack(onClose)
            }}
            aria-label="Close and go back"
            title="Back"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-rule bg-paper text-void no-underline transition-[color,border-color,transform] duration-150 hover:border-ballpoint hover:text-ballpoint active:scale-95 motion-reduce:transform-none"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 14 14"
              className="h-3.5 w-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            >
              <path d="M2 2l10 10M12 2L2 12" />
            </svg>
          </a>
        ) : null}
      </div>
    </header>
  )
}
