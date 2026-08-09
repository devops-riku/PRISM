import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react'
import { useEffect, useState } from 'react'
import { currentUser, onSession, signOut } from '../lib/auth'
import { goBack } from '../lib/navigation'
import type { Theme } from '../lib/theme'
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
  /** Which palette the studio is in right now. */
  theme: Theme
  /** Flips it. `App.tsx` owns the state; this only ever calls the setter. */
  onToggleTheme: () => void
  /**
   * Which container the shell beneath this route is capped at. The header
   * carries its own width rather than inheriting one, so it has to be told -
   * get it wrong and the wordmark and the avatar stop sitting above the
   * content they belong to. `'app'` is every working screen; `'sheet'` is
   * the quotation route, the one page still at the reading measure.
   */
  width: 'app' | 'sheet'
}

export default function AppHeader({
  screenName = '',
  studioName = '',
  onClose = '',
  theme,
  onToggleTheme,
  width,
}: AppHeaderProps) {
  const studio = studioName.trim() || 'PRISM'

  // Who is signed in, where there are accounts at all. On an install without
  // them this stays null and the menu says nothing about sessions - there is
  // nothing to say.
  const [user, setUser] = useState(() => currentUser())
  useEffect(() => onSession((session) => setUser(session?.user || null)), [])
  const email = user?.email || ''

  return (
    // The surface: full-bleed, always. Its background and hairline are what
    // read as "a bar across the top of the screen" rather than a panel with
    // canvas either side, so nothing here caps its width - the cap belongs to
    // the row inside it, which is the one thing this component was actually
    // asked to align with the content beneath.
    <header className="w-full shrink-0 border-b border-hairline bg-paper/60">
      {/* The row: capped and aligned exactly as the shell's own `<main>` is,
          so the wordmark and the avatar sit above the content they belong
          to. `width` picks the cap; the horizontal padding (`px-4 sm:px-6`)
          is the one `<main>` gives itself on every route this ships on. The
          vertical padding is the bar's own and deliberately thin - `<main>`
          no longer lends it a top margin the way it used to, so this is the
          whole of the bar's height now, not half of it. */}
      <div
        className={`mx-auto flex w-full items-center justify-between gap-2 px-4 py-2 sm:gap-4 sm:px-6 sm:py-4 ${width === 'sheet' ? 'max-w-sheet' : 'max-w-app'}`}
      >
        <div className="flex min-w-0 items-center gap-3">
          <a
            href="#/"
            className={`${DISPLAY} shrink-0 text-[15px] tracking-[-0.01em] text-ink no-underline hover:text-ballpoint`}
          >
            PRISM
          </a>
          <div className="hidden min-w-0 sm:block">
            <WorkspaceMenu />
          </div>
          {screenName ? (
            <div className="hidden min-w-0 items-center gap-3 lg:flex">
              <span aria-hidden="true" className="font-label text-[12px] text-faint">
                /
              </span>
              <p className="truncate font-label text-[12px] uppercase tracking-[0.14em] text-faint">
                {screenName}
              </p>
            </div>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-1 sm:gap-2">
          {/* One field that reaches everything. It sits beside the profile rather
              than in the middle: it is a way through the app, not the subject of
              the page. */}
          <CommandBar />
          {/* The bell shows everywhere, including the front page: it is the one
              piece of chrome that is about you rather than about the screen. */}
          <NotificationBell />
          {/* The icon shows what pressing it gives you, not what you are in: a
              moon in dark mode would describe its own state and offer nothing
              to act on. */}
          <button
            type="button"
            onClick={onToggleTheme}
            aria-label={theme === 'light' ? 'Switch to dark' : 'Switch to light'}
            title={theme === 'light' ? 'Switch to dark' : 'Switch to light'}
            /* `neu` is on trial here and nowhere else — see its comment in
               index.css, including what is wrong with the style. This control
               is a reasonable first subject: it is a toggle, so the pressed
               inversion means something, and its job is already given away by
               the icon rather than by the shape around it. */
            className="neu flex h-10 w-10 items-center justify-center rounded-[10px] text-void transition-colors hover:text-ink sm:h-auto sm:w-auto sm:p-2"
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
              {theme === 'light' ? (
                <path d="M20 13a8 8 0 1 1-9-9 6.5 6.5 0 0 0 9 9Z" />
              ) : (
                <>
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
                </>
              )}
            </svg>
          </button>
          <Menu>
            <MenuButton
              aria-label={`${studio} — menu`}
              className="flex h-10 items-center gap-1 rounded-md border border-transparent p-1 transition-[background-color,border-color] duration-150 hover:bg-paper data-[open]:border-rule data-[open]:bg-paper sm:gap-2 sm:pr-2"
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ballpoint font-label text-[11px] font-medium tracking-[0.06em] text-paper">
                {initialsFor(studio)}
              </span>
              <span className="hidden max-w-[16ch] truncate font-body text-[13px] text-body xl:block">
                {studio}
              </span>
              <svg
                aria-hidden="true"
                viewBox="0 0 12 8"
                className="hidden h-2 w-3 flex-none text-faint sm:block"
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
              className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-rule bg-paper text-void no-underline transition-[color,border-color,transform] duration-150 hover:text-ballpoint active:scale-95 motion-reduce:transform-none sm:h-8 sm:w-8"
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
      </div>
    </header>
  )
}
