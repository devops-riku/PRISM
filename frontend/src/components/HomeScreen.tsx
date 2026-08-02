import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { listJobs } from '../lib/api'
import { useRole } from '../lib/role'
import { DISPLAY } from './tokens'

/**
 * The front page: five places to go, and the one number worth knowing before
 * you pick one.
 *
 * It replaces a row of navigation links at the top of every screen. Links that
 * small are a menu you read; cards this size are a choice you make — and on the
 * screen you land on with nothing in flight, choosing is the only job. Each
 * page now carries a close button back to here rather than a strip of the other
 * places you are not.
 *
 * The count is read once on arrival and is a door rather than decoration:
 * "Being prepared" is the way in to the jobs page. It is the only figure here
 * because it is the only one that changes while you are looking at it - "on
 * file" said what the PAD Quotations card already said, one row above it.
 *
 * A pill above the grid decides which side is showing. For You is the grid
 * above, exactly as it was; For Client is where a client's own request
 * starts, and where it waits once sent. The count at the foot only ever reads
 * the studio's own queue - a client's request does not have one yet.
 */

//: What the studio does for itself. The existing five, unchanged - Settings
//: is still filtered out for anyone who is not an admin.
const FOR_YOU = [
  {
    href: '#/pad',
    label: 'Create PAD',
    detail: 'Project Assessment & Documentation',
    icon: (
      <>
        <rect x="3.5" y="2.5" width="13" height="19" rx="2.5" />
        <path d="M7 8h6M7 12h6M7 16h3" />
      </>
    ),
  },
  {
    href: '#/quotations',
    label: 'PAD Quotations',
    detail: 'Everything already prepared',
    icon: (
      <>
        <path d="M7 4.5h9.5a2 2 0 0 1 2 2V19a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6.5a2 2 0 0 1 2-2Z" />
        <path d="M9 9h6M9 13h6M9 17h3.5" />
      </>
    ),
  },
  {
    href: '#/proposals',
    label: 'Build Proposal',
    detail: 'Turn a quotation into a signed document',
    icon: (
      <>
        <path d="M6 3.5h8.5L19 8v12.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 20.5v-15A1.5 1.5 0 0 1 6.5 4Z" />
        <path d="M14 3.5V8h4.5" />
        <path d="M8.5 17.5h7" />
      </>
    ),
  },
  {
    href: '#/documents',
    label: 'Proposals',
    detail: 'Everything already built',
    icon: (
      <>
        <path d="M4.5 6.5h11a1.5 1.5 0 0 1 1.5 1.5v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 19V8a1.5 1.5 0 0 1 1.5-1.5Z" />
        <path d="M7 3.5h10A2.5 2.5 0 0 1 19.5 6v10" />
        <path d="M6.5 11h7M6.5 15h4.5" />
      </>
    ),
  },
  {
    href: '#/settings',
    label: 'Settings',
    detail: 'Defaults and the rate card',
    icon: (
      <>
        <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
        <circle cx="16" cy="7" r="2.2" />
        <circle cx="8" cy="17" r="2.2" />
      </>
    ),
  },
]

//: What the studio does with a client in the room. Two for now; the client
//: link and the sent-quotation queue join them when Stage 2 ships.
const FOR_CLIENT = [
  {
    href: '#/intakes/new',
    label: 'New client request',
    detail: 'What they asked for, in their words',
    icon: (
      <>
        <path d="M4 6.5h16v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" />
        <path d="M4 7l8 6 8-6" />
      </>
    ),
  },
  {
    href: '#/intakes',
    label: 'Client requests',
    detail: 'What is waiting on you',
    icon: (
      <>
        <path d="M4.5 5.5h15M4.5 12h15M4.5 18.5h9" />
      </>
    ),
  },
]

//: The pill's two stops, in the order arrow keys move through them.
const SIDES = [
  ['you', 'For You'],
  ['client', 'For Client'],
] as const

export default function HomeScreen() {
  const [running, setRunning] = useState<number | null>(null)
  const [side, setSide] = useState<'you' | 'client'>('you')
  const destinations = side === 'you' ? FOR_YOU : FOR_CLIENT
  const pill = useRef<HTMLDivElement | null>(null)
  // Settings belongs to whoever runs the workspace. A member is not shown a
  // door that opens onto a 403.
  const { isAdmin } = useRole()

  // A role="tablist" promises the WAI-ARIA tab pattern: the arrows move
  // between tabs and only the active one sits in the page's Tab order. Two
  // stops side by side means only the horizontal arrows are meaningful here
  // (the APG reserves Up/Down for a vertical tablist, which this is not).
  const moveSide = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (!step) return
    event.preventDefault()
    const index = SIDES.findIndex(([id]) => id === side)
    const [next] = SIDES[(index + step + SIDES.length) % SIDES.length]
    setSide(next)
    // Focus follows selection, so the arrow keys feel like a switch rather
    // than a cursor moving over inert labels.
    pill.current?.querySelector<HTMLElement>(`[data-tab="${next}"]`)?.focus()
  }

  useEffect(() => {
    let live = true
    listJobs()
      .then((jobs) => {
        if (!live) return
        setRunning(
          Array.isArray(jobs)
            ? jobs.filter((job) => job.state === 'queued' || job.state === 'running').length
            : null,
        )
      })
      .catch(() => live && setRunning(null))
    return () => {
      live = false
    }
  }, [])

  const figure = (value: number | null) => (value === null ? '—' : value)

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center py-6">
      <div className="w-full max-w-[62rem]">
        <div className="text-center">
          {/* The name in full, and only here. The header keeps the acronym,
              which is what people say - the front page is the one screen with
              room to say what it stands for. */}
          <h1
            className={`${DISPLAY} mx-auto max-w-[30ch] text-[19px] leading-[1.3] text-ink sm:text-[22px]`}
          >
            Project Requirements &amp; Intelligent Solution Management
          </h1>
          <p className="mx-auto mt-3 max-w-[52ch] font-body text-[15px] text-void">
            A quotation for the client, requirements for whoever builds it.
          </p>
        </div>

        <div className="mt-10 mb-6 flex justify-center">
          <div ref={pill} className="pill" role="tablist" aria-label="Whose work" onKeyDown={moveSide}>
            {SIDES.map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                id={`home-tab-${id}`}
                data-tab={id}
                aria-selected={side === id}
                aria-controls="home-panel"
                tabIndex={side === id ? 0 : -1}
                onClick={() => setSide(id)}
                className={`pill__tab ${side === id ? 'pill__tab--on' : ''}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <nav
          id="home-panel"
          role="tabpanel"
          aria-labelledby={`home-tab-${side}`}
          className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5"
        >
          {destinations.filter((place) => isAdmin || place.href !== '#/settings').map(
            (place) => (
            <a
              key={place.href}
              href={place.href}
              // Square, so the five read as one set: the longest label used to
              // set the height for all of them, and a card that is taller
              // because its words are longer looks like it matters more.
              className="group flex aspect-square flex-col items-center justify-center rounded-xl border border-rule bg-paper px-4 py-5 text-center no-underline shadow-sheet transition-[border-color,transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:border-ballpoint hover:shadow-raised active:translate-y-0 active:shadow-sheet motion-reduce:transform-none motion-reduce:transition-none"
            >
              <span className="flex h-11 w-11 items-center justify-center rounded-lg bg-accent-soft text-ballpoint">
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  className="h-[22px] w-[22px]"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  {place.icon}
                </svg>
              </span>
              {/* Both text blocks reserve two lines whether they use them or
                  not. Without that, a one-line label makes its whole block
                  shorter, the centred block shifts down, and the five marks end
                  up at five different heights. */}
              <span className="mt-4 flex min-h-[2.2em] items-center font-label text-[12px] font-medium uppercase leading-[1.35] tracking-[0.14em] text-ink group-hover:text-ballpoint">
                {place.label}
              </span>
              <span className="mt-1 flex min-h-[3em] items-start font-body text-[13px] leading-[1.5] text-void">
                {place.detail}
              </span>
            </a>
            ),
          )}
        </nav>

        {/* Only the one figure that changes while you watch. "On file" said the
            same thing as the PAD Quotations card directly above it, and a
            number repeated is a number nobody reads. */}
        <div className="mx-auto mt-6 grid max-w-[17rem] grid-cols-1 gap-4">
          <a
            href="#/jobs"
            className={`rounded-xl px-5 py-4 text-center no-underline transition-colors duration-150 ${
              running
                ? 'bg-ballpoint hover:bg-accent-deep'
                : 'bg-duplicate hover:bg-accent-soft'
            }`}
          >
            <span
              className={`block font-label text-[30px] leading-none tabular-nums ${
                running ? 'text-paper' : 'text-ink'
              }`}
            >
              {figure(running)}
            </span>
            <span
              className={`mt-2 block font-label text-[12px] font-medium uppercase tracking-[0.14em] ${
                running ? 'text-paper/80' : 'text-faint'
              }`}
            >
              Being prepared
            </span>
          </a>
        </div>
      </div>
    </div>
  )
}
