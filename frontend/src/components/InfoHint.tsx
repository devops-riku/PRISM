import { Popover, PopoverButton, PopoverPanel } from '@headlessui/react'
import type { ReactNode } from 'react'

/**
 * The small ⓘ beside a label, and the sentence it hides.
 *
 * The pad used to print its guidance as a paragraph under every label. That
 * reads well the first time and is furniture by the tenth: a studio that has
 * written forty quotations does not need to be told what a scope is, and the
 * text pushed the control it was explaining further down every screen. The
 * words are the same; they are behind a control now instead of in front of
 * the work.
 *
 * WHAT THIS COSTS, because it is a real trade and not a free tidy-up:
 * guidance you cannot see is guidance a first-time user does not know exists.
 * That is the argument for the icon being visible rather than the text
 * appearing on hover over the label - there is always something on screen
 * saying "there is more to know here", and it is in the same place every
 * time.
 *
 * A POPOVER, NOT A `title` ATTRIBUTE. A native tooltip cannot be opened by
 * touch at all, takes about a second to appear, cannot be styled, and is
 * skipped by several screen readers. This is a real button: it opens on
 * click, so it works on a phone; Escape and an outside click close it; and
 * Headless UI wires `aria-expanded` and `aria-controls` between the two
 * halves, so the panel is announced as the button's content rather than as
 * loose text somewhere in the page.
 */

type InfoHintProps = {
  /** What the icon is about, for the button's accessible name. A screen
   *  reader announcing forty buttons all called "More information" has told
   *  somebody nothing, so this is required rather than optional. */
  label: string
  children: ReactNode
}

export default function InfoHint({ label, children }: InfoHintProps) {
  return (
    <Popover className="relative inline-flex">
      {/* `align-middle` and the explicit box: this sits inline after a label
          whose text is 12px uppercase, and without a fixed size it would
          shift that label's baseline. */}
      <PopoverButton
        aria-label={`About ${label}`}
        className="inline-flex h-[18px] w-[18px] flex-none items-center justify-center rounded-full align-middle text-faint transition-colors duration-150 hover:text-ink focus:outline-none focus-visible:shadow-[var(--shadow-ring)] data-[open]:text-ink"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className="h-[15px] w-[15px]"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <circle cx="10" cy="10" r="7.25" />
          <path d="M10 9v4.4" strokeLinecap="round" />
          <path d="M10 6.4h.01" strokeLinecap="round" strokeWidth="1.9" />
        </svg>
      </PopoverButton>

      {/* `anchor` puts this in a portal at the end of <body>, which is the
          only way it escapes the `overflow-hidden` shells the whole app is
          built from - a panel positioned inside one would be clipped by it.
          `bottom start` keeps its left edge on the icon; the viewport padding
          lets it flip up when the field is near the bottom of the pad. */}
      <PopoverPanel
        anchor={{ to: 'bottom start', gap: 8, padding: 12 }}
        className="z-50 w-[min(20rem,calc(100vw-1.5rem))] rounded-xl border border-rule bg-paper p-3.5 shadow-raised"
      >
        {/* Deliberately NOT `text-void`. This is the one place the sentence
            appears now, so it is read rather than skimmed past, and it takes
            the body colour like anything else somebody is meant to read. */}
        <p className="font-body text-[13px] leading-[1.6] text-body">{children}</p>
      </PopoverPanel>
    </Popover>
  )
}
