import type { CSSProperties } from 'react'
import AuthScreen from './AuthScreen'
import { DISPLAY, MONO_LABEL } from './tokens'

/**
 * What a signed-out visitor sees.
 *
 * `AuthGate` used to put `AuthScreen` alone in the middle of an empty canvas.
 * That is correct for a tool you already use and wrong for the first time
 * anybody sees PRISM: a bare sign-in card says "you are locked out" where the
 * only thing worth saying is what this is for. So the card keeps its job and
 * moves to one side of a page that answers the question first.
 *
 * ONE SCREEN, NO SCROLL, like every other pinned shell in the app. A landing
 * page that scrolls is a page with a second half nobody reads, and everything
 * here is meant to be readable in one look. That constraint is what keeps it
 * minimal - there is no room for a third section, so there isn't one.
 *
 * EVERY CLAIM BELOW IS TRUE OF THE CODE, checked rather than written. The
 * rate card is `backend/app/prompts.py`'s binding rate card; the clauses are
 * the studio's own policies inserted verbatim by the renderer; the figures
 * really do all come from the quotation, which is the constraint that stops
 * the model typing a number into prose. Marketing copy that overstates a
 * product is a bug with a longer feedback loop.
 */

/** The three things worth knowing, in the order they matter. */
const PROOF = [
  ['Priced from your rate card', 'Not invented. The card is binding, and every line is costed against it.'],
  ['Your clauses, word for word', 'Validity, payment, ownership, warranty — inserted exactly as you wrote them.'],
  ['Every figure traces back', 'Numbers are printed from the quotation, never restated into prose.'],
] as const

export default function LandingScreen() {
  return (
    // PINNED ONLY WHERE IT FITS. `h-dvh overflow-hidden` is right for the
    // two-column desktop layout and actively broken below `lg`, where the
    // columns become two stacked rows: a fixed-height grid gives each row a
    // share of the viewport, the content overflows its track, and the rows
    // paint over each other. Measured at 900x800 - the headline clipped off
    // the top, the proof list running under the sign-in card, and the card
    // itself cut off at the bottom with nothing able to scroll it back.
    //
    // So the one-screen rule applies at `lg` and up, and a narrow screen gets
    // an ordinary scrolling page. That is not a compromise of the principle:
    // one screen means one idea in view at a time, and on a phone the hero
    // and the form are two.
    <div className="page-glow grid min-h-dvh grid-cols-1 bg-canvas font-body text-body lg:h-dvh lg:grid-cols-[1.15fr_1fr] lg:overflow-hidden">
      {/* The argument. On a narrow screen this is the whole page and the card
          below it is reached by the only scroll in the layout — see the
          wrapper's own comment. */}
      <section className="flex min-h-0 flex-col justify-center px-7 py-10 sm:px-12 lg:px-16">
        {/* 44rem, not 34rem, and the number is set by the headline rather than
            by taste. The `<br />` below asks for exactly two lines; at 34rem
            the longer of them ("and technical specification.") wrapped again
            and the hero rendered as FOUR lines at every desktop width. A
            forced break only reads as a designed break if each side of it
            actually fits. */}
        <div className="mx-auto w-full max-w-[44rem]">
          <div className="flex items-center gap-3 rise-in" style={{ '--i': 0 } as CSSProperties}>
            <PrismMark />
            <span className={`${MONO_LABEL} text-ink`}>PRISM</span>
          </div>

          {/* The one big moment on the page. `clamp` rather than a stack of
              breakpoints: this is the only type in the app that wants to be
              fluid, because it is the only line whose whole job is scale. */}
          <h1
            className={`${DISPLAY} rise-in mt-8 text-[clamp(1.85rem,3.4vw,3rem)] leading-[1.08] tracking-[-0.03em] text-ink`}
            style={{ '--i': 1 } as CSSProperties}
          >
            From scope to quotation
            <br />
            and technical specification.
          </h1>

          <p
            // No `ch` cap, and `text-pretty` for where it does wrap. At 46ch
            // this broke one word before the end and left "documents." alone
            // on a line. The sentence is 73 characters, which fits the 44rem
            // column outright at desktop widths; below that `text-pretty`
            // stops the browser leaving a single-word last line.
            className="rise-in mt-6 font-body text-[16px] leading-[1.65] text-pretty text-void"
            style={{ '--i': 2 } as CSSProperties}
          >
            Turn one project submission into two consistent, ready-to-send documents.
          </p>

          <dl className="mt-10 border-t border-hairline">
            {PROOF.map(([term, detail], index) => (
              <div
                key={term}
                className="rise-in border-b border-hairline py-4"
                style={{ '--i': index + 3 } as CSSProperties}
              >
                <dt className="font-body text-[14.5px] font-medium leading-[1.4] text-ink">
                  {term}
                </dt>
                <dd className="mt-1 font-body text-[13.5px] leading-[1.55] text-void">{detail}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* The card. The inner scroll is `lg:` only, and pairs with the pinning
          above: on a wide screen the PAGE never scrolls and a short viewport
          makes the form scroll inside its own column instead. Below `lg` the
          page itself scrolls, and a nested scroller there would be a second
          scrollbar inside the first - the thing that makes a form on a phone
          feel like a trap.

          The left rule and the fill are also `lg:` only, for the same reason:
          they separate two columns, and stacked there is only an edge. */}
      <section className="flex min-h-0 items-center justify-center px-7 pb-12 lg:overflow-y-auto lg:border-l lg:border-hairline lg:bg-duplicate/40 lg:py-10">
        <div className="rise-in w-full max-w-[24rem]" style={{ '--i': 2 } as CSSProperties}>
          <AuthScreen />
        </div>
      </section>
    </div>
  )
}

/** The product's own mark, at wordmark size: a beam entering a prism and
 *  leaving it split. The three refracted rays keep their colours in both
 *  themes for the reason `index.html` gives at greater length — a prism that
 *  splits white light into grey is not a prism. Everything else takes a
 *  token, so the mark follows the palette without the rays being touched. */
function PrismMark() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 44 30"
      className="h-[26px] w-[38px] flex-none"
      fill="none"
      strokeLinecap="round"
    >
      <path d="M2 15h13" stroke="currentColor" strokeWidth="1.6" className="text-ink" />
      <path
        d="M22 5 L15 24 Q14 26 16 26 L28 26 Q30 26 29 24 L22.6 5 Q22 3.6 21.4 5 Z"
        stroke="currentColor"
        strokeWidth="1.6"
        className="text-ballpoint"
      />
      <path d="M29 12l12-4" stroke="#c96a63" strokeWidth="1.6" />
      <path d="M30 15h12" stroke="#a8b862" strokeWidth="1.6" />
      <path d="M29 18l12 4" stroke="#d69433" strokeWidth="1.6" />
    </svg>
  )
}
