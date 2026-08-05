import type { ReactNode } from 'react'
import { WELL } from './tokens'
import type { QuotationKind } from '../types'

/**
 * What kind of work is being quoted.
 *
 * It is the pad's first question because it is the one that shapes every answer
 * after it: the words the model writes in, the roles it prices, and the whole
 * second document. An accounting engagement has no tech stack, and asking this
 * last would mean asking for one first.
 *
 * The ids are the server's — app/kinds.py — and the two lists have to agree.
 * There is no endpoint for them on purpose: six words that change about once a
 * year are not worth a request on every page load, and a picker that cannot
 * render until the network answers is a picker that flickers.
 */

/** One choice on the plate: the server's id, what the pad calls it, and its mark. */
export type KindOption = {
  id: QuotationKind
  label: string
  hint: string
  icon: ReactNode
}

export const KINDS: KindOption[] = [
  {
    id: 'software',
    label: 'Web / Software Development',
    hint: 'Sites, apps, integrations',
    icon: (
      <>
        <path d="M9 8.5 5.5 12 9 15.5" />
        <path d="M15 8.5 18.5 12 15 15.5" />
        <path d="M13 6.5 11 17.5" />
      </>
    ),
  },
  {
    id: 'accounting',
    label: 'Accounting & Finance',
    hint: 'Bookkeeping, audit, compliance',
    icon: (
      <>
        <rect x="5" y="3.5" width="14" height="17" rx="2" />
        <path d="M8.5 8h7M8.5 12h3M8.5 16h3M14.5 12v4M12.5 14h4" />
      </>
    ),
  },
  {
    id: 'engineering',
    label: 'Engineering',
    hint: 'Civil, mechanical, electrical',
    icon: (
      <>
        <path d="M4 19.5 12 4l8 15.5Z" />
        <path d="M8.5 13h7" />
      </>
    ),
  },
  {
    id: 'design',
    label: 'Design',
    hint: 'Brand, product, interiors',
    icon: (
      <>
        <circle cx="12" cy="12" r="8" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
  },
  {
    id: 'marketing',
    label: 'Marketing',
    hint: 'Campaigns, content, media',
    icon: (
      <>
        <path d="M4 10.5v3a1.5 1.5 0 0 0 1.5 1.5H8l6 4V5l-6 4H5.5A1.5 1.5 0 0 0 4 10.5Z" />
        <path d="M17.5 9.5a4 4 0 0 1 0 5" />
      </>
    ),
  },
  {
    id: 'other',
    label: 'Something else',
    hint: 'Anything you name yourself',
    icon: (
      <>
        <circle cx="12" cy="12" r="8" />
        <path d="M9.5 9.8a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2-2.5 3.6" />
        <path d="M12 17.4h.01" />
      </>
    ),
  },
]

type KindPickerProps = {
  value: QuotationKind
  label: string
  onChange: (id: QuotationKind) => void
  onLabel: (label: string) => void
  disabled?: boolean
}

export default function KindPicker({ value, label, onChange, onLabel, disabled }: KindPickerProps) {
  return (
    // `@container` here rather than on the grid, because a container sizes its
    // descendants and never itself - an element cannot answer its own query.
    //
    // Container queries, not viewport ones, and the difference is visible.
    // `sm:`/`xl:` ask how wide the *window* is, which is the right question
    // only when this picker is as wide as the page. It is not: the pad gives it
    // a wide column and the client's own form gives it a card far narrower than
    // the window. On a 1920px screen the viewport rule said three across while
    // the card was 736px, so "Web / Software Development" wrapped onto three
    // lines and every hint truncated mid-word. `@container` asks how wide
    // *this* is, which is the question that was always meant.
    <div className="@container">
      <div
        role="radiogroup"
        aria-label="What kind of work is this?"
        className="grid grid-cols-1 gap-2 @md:grid-cols-2 @3xl:grid-cols-3"
      >
        {KINDS.map((kind) => {
          const chosen = value === kind.id
          return (
            <button
              key={kind.id}
              type="button"
              role="radio"
              aria-checked={chosen}
              disabled={disabled}
              onClick={() => onChange(kind.id)}
              className={`flex h-full items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-[border-color,background-color,transform] duration-150 ease-press active:scale-[0.995] disabled:opacity-60 motion-reduce:transform-none ${
                chosen
                  ? 'border-ballpoint bg-accent-soft'
                  : 'border-rule bg-paper hover:bg-duplicate'
              }`}
            >
              <span
                className={`mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-lg ${
                  chosen ? 'bg-ballpoint text-paper' : 'bg-duplicate text-ballpoint'
                }`}
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  className="h-[17px] w-[17px]"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  {kind.icon}
                </svg>
              </span>
              <span className="min-w-0">
                <span
                  className={`flex min-h-[2.6em] items-center font-body text-[14px] leading-[1.3] ${
                    chosen ? 'text-ink' : 'text-body'
                  }`}
                >
                  {kind.label}
                </span>
                {/* `text-void`, not `text-faint`. This blurb sits on two
                    different grounds - `bg-paper` unchosen, `bg-accent-soft`
                    chosen - and on the chosen one `faint` has measured 3.03,
                    then 4.2, across the last three palettes. Both are under
                    the 4.5 that 12.5px body text needs; the size is what makes
                    it a failure, since 3:1 only buys you 18px or 14px bold.
                    `void` clears it on both grounds in both themes. */}
                <span className="block truncate font-body text-[12.5px] leading-[1.4] text-void">
                  {kind.hint}
                </span>
              </span>
            </button>
          )
        })}
      </div>

      {value === 'other' ? (
        <div className="mt-2.5 max-w-[420px]">
          <label htmlFor="kind_label" className="sr-only">
            What do you call this kind of work?
          </label>
          <input
            id="kind_label"
            autoFocus
            value={label}
            maxLength={40}
            disabled={disabled}
            onChange={(event) => onLabel(event.target.value)}
            placeholder="Legal, Training, Surveying…"
            className={WELL}
          />
          <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
            Your word for it, printed as the requirements document&rsquo;s title. Leave it empty and
            the document is simply called Project Requirements.
          </p>
        </div>
      ) : null}
    </div>
  )
}
