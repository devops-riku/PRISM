# Shared layouts and screen compositions

The app does not use a router-provided layout component. `App.tsx` chooses hash-routed screens, while the components below define the reusable shell boundaries and the complete signed-out landing composition. `LandingScreen` and `AuthScreen` are included here because they are the visual ground truth for the current root-page redesign; shared controls imported by `AppHeader` are included once in `components.md`.

## AuthGate

- File: `frontend/src/components/AuthGate.tsx`
- Description: Root authentication boundary: waits for session discovery, shows configuration fallback, sends signed-out users to LandingScreen, and otherwise renders the app.
- Key props: `children: ReactNode`

```tsx
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { User } from '@supabase/supabase-js'
import { currentUser, onSession, signInRequired, startSession, supabase } from '../lib/auth'
import LandingScreen from './LandingScreen'

/**
 * Nothing is shown until the question "who is this?" has an answer — on the
 * installs that ask it.
 *
 * An install with no Supabase project configured has no accounts, the API
 * answers everyone, and this renders the app untouched. Putting a sign-in
 * screen in front of an API that would answer a stranger anyway is theatre, and
 * the one thing an auth screen must never be is decorative.
 *
 * Where accounts do exist, the gate holds until Supabase has restored whatever
 * session the browser was keeping. That wait is deliberately silent for the
 * first moment: a sign-in card that flashes up and vanishes because a session
 * was there all along reads as having been signed out.
 */

/** The four answers to "who is this?", including not knowing yet. */
type GateState = 'reading' | 'open' | 'misconfigured' | 'gated'

type AuthGateProps = { children: ReactNode }

export default function AuthGate({ children }: AuthGateProps) {
  const [state, setState] = useState<GateState>('reading')
  const [user, setUser] = useState<User | null>(null)

  useEffect(() => {
    let live = true

    signInRequired()
      .then(async (required) => {
        if (!live) return
        if (!required) {
          setState('open')
          return
        }
        const client = await supabase()
        if (!live) return
        if (!client) {
          // The server says sign-in is required but gave no project to sign in
          // to. Saying so beats an endless spinner.
          setState('misconfigured')
          return
        }
        await startSession()
        if (!live) return
        setUser(currentUser())
        setState('gated')
      })
      .catch(() => live && setState('open'))

    const stop = onSession((session) => {
      if (!live) return
      setUser(session?.user || null)
    })

    return () => {
      live = false
      stop()
    }
  }, [])

  if (state === 'reading') {
    return <div className="h-dvh bg-canvas" aria-busy="true" />
  }

  if (state === 'misconfigured') {
    return (
      <div className="flex h-dvh items-center justify-center bg-canvas px-6 font-body text-body">
        {/* p-7 sm:p-8 — the single-card family's shared padding (AuthGate,
            AuthScreen, ClientClosed, ClientQuotation, ClientShell's offline
            face, ClientWaiting, InviteScreen). ClientForm is the one
            exception, fitted to a fixed viewport budget — see its own
            comment. */}
        <div className="max-w-[34rem] rounded-[18px] border border-rule bg-paper p-7 shadow-raised sm:p-8">
          <p className="font-label text-[12px] uppercase tracking-[0.14em] text-alert">
            Sign-in not finished
          </p>
          <p className="mt-2 font-body text-[15px] leading-[1.6] text-void">
            This API requires a sign-in but did not name an account service to sign in to. Set{' '}
            <code className="font-label text-[13px] text-ink">SUPABASE_URL</code> and{' '}
            <code className="font-label text-[13px] text-ink">SUPABASE_ANON_KEY</code> in{' '}
            <code className="font-label text-[13px] text-ink">backend/.env</code> and restart it.
          </p>
        </div>
      </div>
    )
  }

  if (state === 'gated' && !user) {
    // `LandingScreen`, not a bare `AuthScreen` on an empty canvas. This is
    // the first thing anybody ever sees of PRISM, and a lone sign-in card
    // says "you are locked out" where the only useful thing to say is what
    // the product does. The card is still there, on one side of a page that
    // answers that first; it owns its own pinning and scrolling, so this
    // branch hands over the whole viewport rather than centring anything.
    return <LandingScreen />
  }

  return children
}

```

## LandingScreen

- File: `frontend/src/components/LandingScreen.tsx`
- Description: Signed-out root composition: responsive product narrative on the left and the AuthScreen card on the right.
- Key props: No external props; product proof and auth composition are currently fixed.

```tsx
import type { CSSProperties } from 'react'
import AuthScreen from './AuthScreen'
import PrismMark from './PrismMark'
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
    <div className="grid min-h-dvh grid-cols-1 bg-canvas font-body text-body lg:h-dvh lg:grid-cols-[1.15fr_1fr] lg:overflow-hidden">
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
            <PrismMark size={30} />
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

```

## AuthScreen

- File: `frontend/src/components/AuthScreen.tsx`
- Description: Stateful authentication-card family for password sign-in, email code, sign-up, password reset, and OAuth.
- Key props: No external props; auth view, field values, busy state, and toast state are internal.

```tsx
import PrismMark from './PrismMark'
import { useEffect, useRef, useState } from 'react'
import type { ComponentPropsWithoutRef, ReactNode } from 'react'
import type { Provider, Session } from '@supabase/supabase-js'
import {
  sendEmailCode,
  sendResetLink,
  signInWithPassword,
  signInWithProvider,
  signUpWithPassword,
  verifyEmailCode,
} from '../lib/auth'
import { describeAuthError } from '../lib/authErrors'
import { takeOAuthReturn } from '../lib/oauthReturn'
import { useToasts } from '../lib/useToasts'
import Toaster from './Toaster'
import { DISPLAY, MONO_LABEL, WELL } from './tokens'

/**
 * Getting in.
 *
 * Four screens, one card: sign in, the emailed code, making an account, and
 * getting back in when the password is gone. They share a card because they are
 * one errand — the person in front of them wants to be inside — and moving
 * between them should feel like the same room, not four pages.
 *
 * Everything the screen has to say — a failure, a sent code, an account that
 * now needs confirming — arrives as a toast rather than as a paragraph growing
 * inside the card. Every field here is above the fold, so a message that took
 * up space would move the button the person was reaching for at the moment they
 * reached for it.
 *
 * Failures are named, not summarised: `describeAuthError` turns the ones PRISM
 * recognises into a sentence and a next move, and passes anything else through
 * in the words the service used. An auth screen that says "something went
 * wrong" leaves somebody retyping a password that was never the problem.
 */

/** One way in that is not a password. `id` is Supabase's own name for it. */
type ProviderChoice = {
  id: Provider
  label: string
  short: string
  mark: ReactNode
}

/**
 * Whether the OAuth providers are offered at all.
 *
 * `true` now. It was `false` while neither Google nor Facebook was configured
 * on the Supabase project, with the buttons on screen but dead - the honest
 * middle between hiding them (a studio who signed up expecting Google sees no
 * trace of it and assumes it was dropped) and offering a live button that
 * fails.
 *
 * WHAT THIS FLAG DOES NOT DO, and it matters: it does not turn the providers
 * on. That is a setting on the Supabase project, not in this repository - each
 * provider needs an OAuth app registered with Google or Meta, its client id and
 * secret pasted into Supabase, and this app's origin listed as a redirect URL.
 * With this `true` and that undone, the buttons work and Supabase refuses them.
 * That refusal is now legible rather than misleading: `authErrors.ts` matches
 * "provider is not enabled" ahead of its shared error code, so the screen says
 * the method is not switched on instead of "check the details and try again",
 * which would send somebody hunting for a typo that does not exist.
 *
 * ONE FLAG, not a per-provider pair, because they were blocked on the same
 * thing. If exactly one of the two is ever configured, this needs to become a
 * pair rather than being left `true` with one button that always fails.
 */
const SSO_READY = true

const PROVIDERS: ProviderChoice[] = [
  {
    id: 'google',
    label: 'Continue with Google',
    short: 'Google',
    mark: (
      <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" aria-hidden="true">
        <path
          fill="#4285F4"
          d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.7v3h3.9c2.3-2.1 3.5-5.2 3.5-8.9Z"
        />
        <path
          fill="#34A853"
          d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9H1.4v3.1A12 12 0 0 0 12 24Z"
        />
        <path fill="#FBBC05" d="M5.4 14.4a7.2 7.2 0 0 1 0-4.6V6.7H1.4a12 12 0 0 0 0 10.8l4-3.1Z" />
        <path
          fill="#EA4335"
          d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.4 6.7l4 3.1C6.3 6.9 8.9 4.8 12 4.8Z"
        />
      </svg>
    ),
  },
  {
    id: 'facebook',
    label: 'Continue with Facebook',
    short: 'Facebook',
    mark: (
      <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" aria-hidden="true">
        <path
          fill="#1877F2"
          d="M24 12a12 12 0 1 0-13.9 11.9v-8.4H7.1V12h3V9.4c0-3 1.8-4.6 4.5-4.6 1.3 0 2.6.2 2.6.2v2.9h-1.5c-1.5 0-1.9.9-1.9 1.8V12h3.3l-.5 3.5h-2.8v8.4A12 12 0 0 0 24 12Z"
        />
      </svg>
    ),
  },
]

type CardProps = {
  title: ReactNode
  blurb?: ReactNode
  children: ReactNode
  footer?: ReactNode
}

/** The card every screen sits in. */
function Card({ title, blurb, children, footer }: CardProps) {
  return (
    <div className="no-scrollbar max-h-dvh w-full max-w-[24rem] overflow-y-auto">
      {/* p-7 sm:p-8, shared across the single-card family (see AuthGate.tsx). */}
      <div className="rounded-[18px] border border-rule bg-paper p-7 shadow-raised sm:p-8">
        {/* The mark, not a letter in a box. The `P` tile predated there being
            a logo; now that there is one, the sign-in card is the first place
            anybody sees the product and should show it. */}
        <PrismMark size={34} />
        <h1 className={`${DISPLAY} mt-4 text-[21px] leading-[1.2] tracking-[-0.025em] text-ink`}>
          {title}
        </h1>
        {blurb ? (
          <p className="mt-1.5 font-body text-[13.5px] leading-[1.5] text-void">{blurb}</p>
        ) : null}
        <div className="mt-4">{children}</div>
      </div>
      {footer ? (
        <p className="mt-3 text-center font-body text-[13px] text-void">{footer}</p>
      ) : null}
    </div>
  )
}

/** Everything an `<input>` takes, plus the label and the hint above it. */
type FieldProps = {
  id: string
  label: ReactNode
  hint?: ReactNode
} & Omit<ComponentPropsWithoutRef<'input'>, 'id' | 'label'>

function Field({ id, label, hint, ...rest }: FieldProps) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className={MONO_LABEL}>
          {label}
        </label>
        {hint}
      </div>
      <input id={id} className={`${WELL} mt-2`} {...rest} />
    </div>
  )
}

/**
 * A password you can check before pressing the button.
 *
 * Hidden by default, because somebody else is often looking at the screen; but
 * a field nobody can read is where a typo goes to hide, and retyping a password
 * you cannot see is worse than briefly showing it to a room you chose.
 */
function PasswordField({ id, label, hint, ...rest }: FieldProps) {
  const [shown, setShown] = useState(false)

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className={MONO_LABEL}>
          {label}
        </label>
        {hint}
      </div>
      <div className="relative mt-2">
        <input id={id} type={shown ? 'text' : 'password'} className={`${WELL} pr-11`} {...rest} />
        <button
          type="button"
          onClick={() => setShown((was) => !was)}
          aria-label={shown ? 'Hide the password' : 'Show the password'}
          aria-pressed={shown}
          title={shown ? 'Hide' : 'Show'}
          className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-[11px] text-faint transition-colors duration-150 hover:text-ballpoint"
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
            <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
            <circle cx="12" cy="12" r="3.2" />
            {shown ? <path d="M4 20 20 4" /> : null}
          </svg>
        </button>
      </div>
    </div>
  )
}

type PrimaryProps = ComponentPropsWithoutRef<'button'>

function Primary({ children, ...rest }: PrimaryProps) {
  return (
    <button
      type="submit"
      className="mt-4 w-full rounded-[11px] bg-ballpoint px-4 py-2.5 font-label text-[13px] uppercase tracking-[0.1em] text-paper shadow-action transition-[transform,background-color] duration-150 ease-press hover:bg-accent-deep active:scale-[0.99] disabled:opacity-45 disabled:shadow-none motion-reduce:transform-none"
      {...rest}
    >
      {children}
    </button>
  )
}

type QuietProps = ComponentPropsWithoutRef<'button'>

function Quiet({ children, ...rest }: QuietProps) {
  return (
    <button
      type="button"
      className="font-body text-[13.5px] text-ballpoint underline underline-offset-[3px] hover:text-accent-deep"
      {...rest}
    >
      {children}
    </button>
  )
}

type CodeRowProps = {
  value: string
  onChange: (next: string) => void
  disabled: boolean
}

/**
 * How long a sign-in code is.
 *
 * SUPABASE DECIDES THIS, not us: Authentication - Providers - Email - "Email
 * OTP Length", which GoTrue allows anywhere from 6 to 10. There is no API
 * that reports the setting, so the client cannot discover it.
 *
 * TWO NUMBERS RATHER THAN ONE, so a mismatch is survivable instead of fatal.
 * It was a single hard-coded 6 in six places, against a project set to 8: the
 * screen drew six boxes that could not hold the code, and the submit guard
 * unlocked at six characters, so pressing Confirm sent the first six digits
 * of an eight-digit code. Supabase rejected that as "Token has expired or is
 * invalid", which is how a fresh code reported itself as expired.
 *
 * `CODE_BOXES` is how many boxes are drawn and must match the dashboard.
 * `CODE_MIN` is GoTrue's floor, and the submit guard uses it rather than the
 * box count on purpose: a code SHORTER than the row still submits, so a
 * dashboard change from 6 to 8 shows as "the last two boxes stay empty"
 * rather than as a Confirm button that never lights. The reverse - a longer
 * code than there are boxes - is the one that cannot be survived, which is
 * why the box count is the number that has to be kept in step.
 */
const CODE_BOXES = 6
const CODE_MIN = 6

/** The boxes. Paste works too — that is what people actually do. */
function CodeRow({ value, onChange, disabled }: CodeRowProps) {
  const boxes = useRef<(HTMLInputElement | null)[]>([])
  const digits = value.padEnd(CODE_BOXES, ' ').slice(0, CODE_BOXES).split('')

  const put = (index: number, next: string) => {
    const cleaned = next.replace(/\D/g, '')
    if (!cleaned) {
      onChange((value.slice(0, index) + ' ' + value.slice(index + 1)).trimEnd())
      return
    }
    if (cleaned.length > 1) {
      onChange(cleaned.slice(0, CODE_BOXES))
      boxes.current[Math.min(cleaned.length, CODE_BOXES - 1)]?.focus()
      return
    }
    const filled = (
      value.padEnd(CODE_BOXES, ' ').slice(0, index) +
      cleaned +
      value.slice(index + 1)
    ).slice(0, CODE_BOXES)
    onChange(filled.trimEnd())
    boxes.current[Math.min(index + 1, CODE_BOXES - 1)]?.focus()
  }

  return (
    /* Every pixel of gap comes straight off the width of each box, and the
       boxes stay square - so the gap is part of how big they end up. `gap-2`
       fits six comfortably; eight needed `gap-1.5`. */
    <div className="flex gap-2">
      {digits.map((digit, index) => (
        <input
          key={index}
          ref={(node) => {
            boxes.current[index] = node
          }}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={CODE_BOXES}
          disabled={disabled}
          aria-label={`Digit ${index + 1}`}
          value={digit.trim()}
          onChange={(event) => put(index, event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Backspace' && !digit.trim() && index > 0) {
              boxes.current[index - 1]?.focus()
            }
          }}
          /* SQUARE, via `aspect-square` rather than a fixed height. A fixed
             `h-12` was right at six boxes and wrong at eight, which is the
             sort of thing that only shows up when the count changes: the
             width is whatever a flex child gets after the gaps come out of a
             24rem card, so the height has to follow the width rather than be
             declared. `min-w-0` lets them actually shrink - a flex item
             defaults to `min-width: auto` and would otherwise refuse to go
             below its content width and overflow the card.

             `--well-border`, not `border-rule`. These boxes are the only
             fields in the app that did not go through `.well`, and inherited a
             DIVIDER colour as their edge: 1.38:1 against the card, on a fill
             that is itself 1.17:1. A row of invisible squares above a button
             that says CONFIRM. WCAG asks 3:1 of a control's boundary and every
             other field here measures 3.6-3.8.
             The inset shadow matches them to the rest too - a thing you type
             into is a dish. */
          className="aspect-square w-full min-w-0 rounded-[11px] border border-[color:var(--well-border)] bg-duplicate text-center font-label text-[18px] tabular-nums text-ink shadow-[var(--shadow-inset)] focus:border-ballpoint focus:outline-none focus:ring-4 focus:ring-ballpoint/12"
        />
      ))}
    </div>
  )
}

/** Which of the four screens the card is showing. */
type AuthView = 'in' | 'code' | 'reset' | 'up'

export default function AuthScreen() {
  const [view, setView] = useState<AuthView>('in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const { toasts, show, dismiss, clear } = useToasts()

  /**
   * Move to another card, dropping the messages the last one raised.
   *
   * "That email already has an account" is help on the sign-up card and noise
   * on the sign-in card it sent you to. Done here rather than in an effect on
   * `view` on purpose: the send-a-code flow changes the view AND raises a note
   * in the same tick, and an effect would clear the note it just asked for.
   */
  const go = (next: AuthView) => {
    clear()
    setView(next)
  }

  const notice = (message: string, hint = '', duration?: number) =>
    show({ tone: 'note', message, hint, duration })

  /**
   * Say why Google or Facebook sent them back.
   *
   * A provider sign-in leaves this page entirely, so its failure cannot arrive
   * as a rejected promise the way every other error on this screen does - it
   * comes back as parameters on a fresh page load, which `lib/oauthReturn.ts`
   * captured before the Supabase client could consume them.
   *
   * No `duration`, unlike `notice`: this one does not time out. Somebody who
   * pressed a button, watched a redirect, and landed back where they started
   * needs the reason to still be there when they look up.
   *
   * `[]` on purpose. `takeOAuthReturn` is one-shot, and StrictMode's double
   * mount in development would otherwise be the only thing standing between
   * this and showing the same failure twice - which is exactly the kind of
   * thing that works in dev and looks broken in production, or the reverse.
   */
  useEffect(() => {
    const returned = takeOAuthReturn()
    if (!returned) return
    const problem = describeAuthError({ code: returned.code, message: returned.message })
    show({ tone: 'alert', message: problem.message, hint: problem.hint })
  }, [])

  /**
   * What to say once an account has been made.
   *
   * With "Confirm email" on, Supabase returns a user and **no session**: the
   * account exists but nobody is signed in until the link is clicked. Nothing
   * on screen changes at that moment, so without this the button simply stops
   * spinning and the person is left looking at a form that appears to have done
   * nothing.
   *
   * Signing up an address that already has an account returns the same
   * shape — no session, and a user carrying no identities. That is Supabase
   * refusing to confirm whether the address is registered, and this says the
   * same thing back rather than helpfully leaking it.
   *
   * With confirmation off a session comes straight back, the app opens behind
   * this card, and a toast would be talking about a screen nobody is on.
   */
  const afterSignUp = (result: { session: Session | null }) => {
    if (result.session) return
    // Straight to the sign-in card: the account exists, and the next thing this
    // person does — after the trip to their inbox — is sign in to it. Leaving
    // them on a filled-in sign-up form invites them to submit it a second time.
    //
    // `setView` rather than `go`, because `go` clears the toasts and the toast
    // is the entire instruction.
    setView('in')
    notice(
      'Check your email to confirm the account.',
      'The link finishes signing you up, then sign in here.',
      12000,
    )
  }

  const run = (work: () => unknown) => {
    setBusy(true)
    Promise.resolve()
      .then(work)
      .catch((failure: unknown) => {
        // Supabase's own wording is written for whoever integrated it. This
        // turns the ones PRISM recognises into something the person at the
        // keyboard can act on, and passes anything else through unchanged.
        const problem = describeAuthError(failure)
        show({ tone: 'alert', message: problem.message, hint: problem.hint })
      })
      .finally(() => setBusy(false))
  }

  const sso = (provider: Provider) => run(() => signInWithProvider(provider))

  const providerRow = (
    <>
      <div className="mt-4 flex items-center gap-3">
        <span className="h-px flex-1 bg-hairline" />
        <span className="font-label text-[11px] uppercase tracking-[0.14em] text-faint">or</span>
        <span className="h-px flex-1 bg-hairline" />
      </div>
      <div className="mt-3 flex gap-2">
        {PROVIDERS.map((provider) => (
          <button
            key={provider.id}
            type="button"
            disabled={busy || !SSO_READY}
            onClick={() => sso(provider.id)}
            /* The title says WHY, not what. On a disabled control the label is
               already visible and "Continue with Google" describes something
               that will not happen - the only useful thing a tooltip can add
               here is the reason it is dead. */
            title={SSO_READY ? provider.label : `${provider.short} sign-in is not available yet`}
            aria-label={
              SSO_READY ? provider.label : `${provider.label} — not available yet`
            }
            className="flex flex-1 items-center justify-center gap-2 rounded-[11px] border border-rule bg-paper px-3 py-2.5 font-body text-[13px] text-body transition-colors duration-150 hover:bg-duplicate disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-paper"
          >
            {provider.mark}
            {provider.short}
          </button>
        ))}
      </div>
      {/* Said on screen, not only in a tooltip. A greyed control with no
          reason next to it reads as "broken" or as "you are not allowed",
          and a title attribute is invisible to anyone on a touch screen -
          which is most people who will ever see this. One line, and it goes
          away by itself when the flag flips. */}
      {SSO_READY ? null : (
        <p className="mt-2 text-center font-body text-[12.5px] leading-[1.5] text-faint">
          Google and Facebook sign-in are not connected yet — use your email
          above.
        </p>
      )}
    </>
  )

  /**
   * Which card is on screen. A function rather than three early returns,
   * because every one of them now shares the toast rail below — and a rail
   * rendered three times is three rails whose animations restart whenever the
   * view changes.
   */
  const card = () => {
    if (view === 'code') {
      return (
        <Card
          title="One more step"
          blurb={`We sent a code to ${email || 'your email'}. Paste works too.`}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault()
              run(() => verifyEmailCode(email, code.replace(/\s/g, '')))
            }}
          >
            <CodeRow value={code} onChange={setCode} disabled={busy} />
            <Primary disabled={busy || code.replace(/\s/g, '').length < CODE_MIN}>
              {busy ? 'Checking' : 'Confirm'}
            </Primary>
          </form>

          <div className="mt-4 flex items-center justify-between">
            <Quiet
              onClick={() =>
                run(() => sendEmailCode(email).then(() => notice('Sent. Check your inbox.')))
              }
            >
              Send another
            </Quiet>
            <Quiet onClick={() => go('in')}>Use a password instead</Quiet>
          </div>
        </Card>
      )
    }

    if (view === 'reset') {
      return (
        <Card
          title="Let’s get you back in"
          blurb="Tell us the email you used and we’ll send a reset link. It’s good for an hour."
          footer={<Quiet onClick={() => go('in')}>Back to sign in</Quiet>}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault()
              run(() =>
                sendResetLink(email).then(() => notice('Sent. The link works for one hour.')),
              )
            }}
          >
            <Field
              id="reset_email"
              label="Email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@studio.com"
            />
            <Primary disabled={busy || !email.trim()}>{busy ? 'Sending' : 'Send the link'}</Primary>
          </form>
        </Card>
      )
    }

    const making = view === 'up'

    return (
      <Card
        title={making ? 'Make an account' : 'Welcome back'}
        blurb={
          making
            ? 'Two fields. You can fill in the rest whenever you feel like it.'
            : 'Sign in and we’ll drop you exactly where you left off.'
        }
        footer={
          making ? (
            <>
              Already have one? <Quiet onClick={() => go('in')}>Sign in</Quiet>
            </>
          ) : (
            <>
              New here? <Quiet onClick={() => go('up')}>Create an account</Quiet>
            </>
          )
        }
      >
        <form
          onSubmit={(event) => {
            event.preventDefault()
            run(() =>
              making
                ? signUpWithPassword(email, password).then(afterSignUp)
                : signInWithPassword(email, password),
            )
          }}
        >
          <Field
            id="auth_email"
            label="Email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@studio.com"
          />
          <div className="mt-3">
            <PasswordField
              id="auth_password"
              label="Password"
              autoComplete={making ? 'new-password' : 'current-password'}
              required
              minLength={6}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••"
            />
            {/* Under the field rather than beside its label: you reach for this
                after typing a password that did not work, not before. */}
            {making ? null : (
              <div className="mt-1.5 text-right">
                <button
                  type="button"
                  onClick={() => go('reset')}
                  className="font-body text-[12.5px] text-ballpoint hover:underline"
                >
                  Forgot your password?
                </button>
              </div>
            )}
          </div>

          <Primary disabled={busy || !email.trim() || password.length < 6}>
            {busy ? 'One moment' : making ? 'Create account' : 'Sign in'}
          </Primary>
        </form>

        <button
          type="button"
          disabled={busy || !email.trim()}
          onClick={() =>
            run(() =>
              sendEmailCode(email).then(() => {
                setView('code')
                notice('Sent. Good for a few minutes.')
              }),
            )
          }
          className="mt-2.5 w-full rounded-[11px] border border-transparent px-4 py-1.5 font-body text-[13px] text-void transition-colors duration-150 hover:bg-duplicate disabled:opacity-45"
        >
          Or email me a sign-in code
        </button>

        {providerRow}
      </Card>
    )
  }

  return (
    <>
      {card()}
      <Toaster toasts={toasts} onDismiss={dismiss} />
    </>
  )
}

```

## AppHeader

- File: `frontend/src/components/AppHeader.tsx`
- Description: Shared authenticated top bar aligned to app or sheet content, with workspace, command, notifications, theme, account, and optional close controls.
- Key props: `screenName?`, `studioName?`, `onClose?`, `theme`, `onToggleTheme`, `width: "app" | "sheet"`

```tsx
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
        className={`mx-auto flex w-full items-center justify-between gap-4 px-4 py-3 sm:px-6 sm:py-4 ${width === 'sheet' ? 'max-w-sheet' : 'max-w-app'}`}
      >
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
            className="neu rounded-[10px] p-2 text-void transition-colors hover:text-ink"
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
              className="flex items-center gap-2 rounded-md border border-transparent py-1 pl-1 pr-2 transition-[background-color,border-color] duration-150 hover:bg-paper data-[open]:border-rule data-[open]:bg-paper"
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
              className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-rule bg-paper text-void no-underline transition-[color,border-color,transform] duration-150 hover:text-ballpoint active:scale-95 motion-reduce:transform-none"
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

```


