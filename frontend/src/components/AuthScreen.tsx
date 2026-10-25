import PrismMark from './PrismMark'
import { useEffect, useRef, useState } from 'react'
import type { ComponentPropsWithoutRef, ReactNode } from 'react'
import type { AuthStep, CodePurpose } from '../lib/auth'
import {
  authConfig,
  resendCode,
  sendEmailCode,
  signInWithPassword,
  signInWithProvider,
  signUpWithPassword,
  startReset,
  takePendingStep,
  verifyEmailCode,
} from '../lib/auth'
import { describeAuthError } from '../lib/authErrors'
import type { AuthProvider, PasswordRules } from '../types'
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

/** How a provider kind is drawn: its name on the button, and its mark. */
type ProviderLook = {
  short: string
  mark: ReactNode
}

/**
 * Which providers get a button: exactly the ones the server says work.
 *
 * `/api/auth/config` lists the providers that are switched on in Zitadel, and
 * a provider is only switched on once its credentials are in — an OAuth app
 * registered with Google or Meta, its client id and secret pasted into the
 * Zitadel console under the PRISM organization. So no credentials means no
 * entry, means no button, and each provider appears by itself the moment it is
 * set up: there is no flag here to flip and nothing to redeploy.
 *
 * That replaced an `SSO_READY` flag that drew both buttons whether or not
 * either could work, and one flag for two providers that would never be
 * configured on the same day.
 */
const LOOKS: Record<string, ProviderLook> = {
  google: {
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
  facebook: {
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
}

/** The rules as one line under a new password, or '' when there are none. */
function rulesHint(rules: PasswordRules | null): string {
  if (!rules) return ''
  const parts = [
    rules.upper && rules.lower ? 'upper and lower case' : rules.upper ? 'an uppercase letter' : rules.lower ? 'a lowercase letter' : '',
    rules.number ? 'a number' : '',
    rules.symbol ? 'a symbol' : '',
  ].filter(Boolean)
  const tail = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0] || ''
  return `At least ${rules.min_length} characters${tail ? `, with ${tail}` : ''}.`
}

/** What the code screen says, by why the code was sent. */
const CODE_COPY: Record<CodePurpose, { title: string; blurb: (email: string) => string }> = {
  code: { title: 'One more step', blurb: (email) => `We sent a code to ${email}. Paste works too.` },
  signup: {
    title: 'Confirm your email',
    blurb: (email) => `We sent a code to ${email}. It confirms the address, then your account is ready.`,
  },
  reset: {
    title: 'Choose a new password',
    blurb: (email) => `If ${email} has an account, a code is on its way. Type it with your new password.`,
  },
  confirm: {
    title: 'Confirm your email first',
    blurb: (email) => `${email} hasn’t been confirmed yet, so we sent it a code.`,
  },
  provider: {
    title: 'Confirm your email',
    blurb: (email) => `Your account didn’t confirm ${email}, so we sent it a code.`,
  },
}

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
      {/* Phones need the extra 16px of field width, especially for the six
          one-time-code targets. The card returns to the single-card family's
          roomier padding once there is enough viewport to spend. */}
      <div className="rounded-[18px] border border-rule bg-paper p-5 shadow-raised sm:p-8">
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
      className="mt-4 min-h-11 w-full rounded-[11px] bg-ballpoint px-4 py-2.5 font-label text-[13px] uppercase tracking-[0.1em] text-paper shadow-action transition-[transform,background-color] duration-150 ease-press hover:bg-accent-deep active:scale-[0.99] disabled:opacity-45 disabled:shadow-none motion-reduce:transform-none sm:min-h-0"
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
      className="inline-flex min-h-11 items-center font-body text-[13.5px] text-ballpoint underline underline-offset-[3px] hover:text-accent-deep sm:min-h-0"
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
 * ZITADEL DECIDES THIS, not us: Default Settings - Secret Generators, "Email
 * verification" and "One-time password by email". Both are set to six digits
 * on zitadel.neptune.ph; out of the box Zitadel sends six letters-and-digits
 * for one and eight digits for the other, which these boxes cannot hold - the
 * boxes take digits only. There is no public API that reports the setting, so
 * the client cannot discover it.
 *
 * TWO NUMBERS RATHER THAN ONE, so a mismatch is survivable instead of fatal.
 * It was a single hard-coded 6 in six places, against a service set to 8: the
 * screen drew six boxes that could not hold the code, and the submit guard
 * unlocked at six characters, so pressing Confirm sent the first six digits
 * of an eight-digit code - which is how a fresh code reported itself as
 * wrong.
 *
 * `CODE_BOXES` is how many boxes are drawn and must match the generators.
 * `CODE_MIN` is the floor, and the submit guard uses it rather than the box
 * count on purpose: a code SHORTER than the row still submits, so a generator
 * change from 8 to 6 shows as "the last two boxes stay empty" rather than as a
 * Confirm button that never lights. The reverse - a longer code than there are
 * boxes - is the one that cannot be survived, which is why the box count is
 * the number that has to be kept in step.
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
    /* Every pixel of gap comes straight off the width of each box. Phones use
       the tighter gap to preserve the targets; the roomier row returns at
       `sm`. */
    <div className="flex gap-1 sm:gap-2">
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
          className="aspect-square min-h-11 w-full min-w-0 rounded-[11px] border border-[color:var(--well-border)] bg-duplicate text-center font-label text-[18px] tabular-nums text-ink shadow-[var(--shadow-inset)] focus:border-ballpoint focus:outline-none focus:ring-4 focus:ring-ballpoint/12 sm:min-h-0"
        />
      ))}
    </div>
  )
}

/** Which of the four screens the card is showing. */
type AuthView = 'in' | 'code' | 'reset' | 'up'

export default function AuthScreen() {
  // A Google or Facebook return can land here mid-errand: the provider did
  // not prove the email, so a code went to it and the card opens on that.
  const [resumed] = useState(() => takePendingStep())
  const [view, setView] = useState<AuthView>(resumed?.step === 'code' ? 'code' : 'in')
  const [email, setEmail] = useState(resumed?.step === 'code' ? resumed.email : '')
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [code, setCode] = useState('')
  const [flow, setFlow] = useState(resumed?.step === 'code' ? resumed.flow : '')
  const [purpose, setPurpose] = useState<CodePurpose>(
    resumed?.step === 'code' ? resumed.purpose : 'code',
  )
  const [providers, setProviders] = useState<AuthProvider[]>([])
  const [rules, setRules] = useState<PasswordRules | null>(null)
  const [busy, setBusy] = useState(false)
  const { toasts, show, dismiss, clear } = useToasts()

  // Which ways in exist, and what a new password must contain. Cached by
  // `authConfig`, which the gate has already read, so this costs no request.
  useEffect(() => {
    let live = true
    authConfig()
      .then((found) => {
        if (!live) return
        setProviders(found.providers || [])
        setRules(found.password_rules)
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [])

  /**
   * Move to another card, dropping the messages the last one raised.
   *
   * "That email and password don't match" is help on the sign-in card and
   * noise on the reset card it sent you to. Done here rather than in an effect
   * on `view` on purpose: the send-a-code flow changes the view AND raises a
   * note in the same tick, and an effect would clear the note it just asked
   * for.
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
   * captured at boot and cleaned off the URL.
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
   * Where a way in left things. Signed in needs nothing: the gate is listening
   * and opens the app behind this card. A code means the code card, for this
   * flow, with a note — nothing else on screen changes at that moment, and
   * without one the button simply stops spinning on a form that appears to
   * have done nothing.
   *
   * `setView` rather than `go`, because `go` clears the toasts and the toast
   * is the entire instruction.
   */
  const after = (step: AuthStep) => {
    if (step.step !== 'code') return
    setFlow(step.flow)
    setPurpose(step.purpose)
    setEmail(step.email || email)
    setCode('')
    setNewPassword('')
    setView('code')
    notice('Sent. Check your inbox.', 'The code is good for ten minutes.', 8000)
  }

  const run = (work: () => unknown) => {
    setBusy(true)
    Promise.resolve()
      .then(work)
      .catch((failure: unknown) => {
        const problem = describeAuthError(failure)
        show({ tone: 'alert', message: problem.message, hint: problem.hint })
        // A flow the server no longer holds cannot be finished from this
        // card; the next useful thing is the card that starts a new one.
        const said = (failure as { code?: string } | null)?.code
        if (said === 'SIGN_IN_EXPIRED' || said === 'TOO_MANY_TRIES') {
          setView(purpose === 'reset' ? 'reset' : 'in')
        }
      })
      .finally(() => setBusy(false))
  }

  const sso = (provider: string) => run(() => signInWithProvider(provider))

  const shown = providers.filter((provider) => provider.id)
  const providerRow = shown.length ? (
    <>
      <div className="mt-4 flex items-center gap-3">
        <span className="h-px flex-1 bg-hairline" />
        <span className="font-label text-[11px] uppercase tracking-[0.14em] text-faint">or</span>
        <span className="h-px flex-1 bg-hairline" />
      </div>
      <div className="mt-3 flex flex-col gap-2 min-[360px]:flex-row">
        {shown.map((provider) => {
          const look = LOOKS[provider.kind]
          const short = look?.short || provider.name || 'Single sign-on'
          return (
            <button
              key={provider.id}
              type="button"
              disabled={busy}
              onClick={() => sso(provider.id)}
              title={`Continue with ${short}`}
              aria-label={`Continue with ${short}`}
              className="flex min-h-11 w-full flex-1 items-center justify-center gap-2 rounded-[11px] border border-rule bg-paper px-3 py-2.5 font-body text-[13px] text-body transition-colors duration-150 hover:bg-duplicate disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-paper sm:min-h-0"
            >
              {look?.mark || null}
              {short}
            </button>
          )
        })}
      </div>
    </>
  ) : null

  /**
   * Which card is on screen. A function rather than three early returns,
   * because every one of them now shares the toast rail below — and a rail
   * rendered three times is three rails whose animations restart whenever the
   * view changes.
   */
  const card = () => {
    if (view === 'code') {
      const copy = CODE_COPY[purpose] || CODE_COPY.code
      const resetting = purpose === 'reset'
      const typed = code.replace(/\s/g, '')
      return (
        <Card title={copy.title} blurb={copy.blurb(email || 'your email')}>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              run(() => verifyEmailCode(flow, typed, resetting ? newPassword : ''))
            }}
          >
            <CodeRow value={code} onChange={setCode} disabled={busy} />
            {resetting ? (
              <div className="mt-3">
                <PasswordField
                  id="reset_password"
                  label="New password"
                  autoComplete="new-password"
                  required
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  placeholder="••••••••"
                />
                {rulesHint(rules) ? (
                  <p className="mt-1.5 font-body text-[12.5px] leading-[1.5] text-faint">{rulesHint(rules)}</p>
                ) : null}
              </div>
            ) : null}
            <Primary disabled={busy || typed.length < CODE_MIN || (resetting && !newPassword)}>
              {busy ? 'Checking' : resetting ? 'Set password' : 'Confirm'}
            </Primary>
          </form>

          <div className="mt-4 flex flex-col items-start gap-1 min-[360px]:flex-row min-[360px]:items-center min-[360px]:justify-between min-[360px]:gap-3">
            <Quiet
              onClick={() =>
                run(() => resendCode(flow).then(() => notice('Sent. Earlier codes stop working.')))
              }
            >
              Send another
            </Quiet>
            <Quiet onClick={() => go('in')}>{resetting ? 'Back to sign in' : 'Use a password instead'}</Quiet>
          </div>
        </Card>
      )
    }

    if (view === 'reset') {
      return (
        <Card
          title="Let’s get you back in"
          blurb="Tell us the email you used and we’ll send a code. You’ll choose the new password with it."
          footer={<Quiet onClick={() => go('in')}>Back to sign in</Quiet>}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault()
              run(() => startReset(email).then(after))
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
            <Primary disabled={busy || !email.trim()}>{busy ? 'Sending' : 'Send the code'}</Primary>
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
              (making ? signUpWithPassword(email, password) : signInWithPassword(email, password)).then(
                after,
              ),
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
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••"
            />
            {/* Under the field rather than beside its label: you reach for this
                after typing a password that did not work, not before. */}
            {making ? (
              rulesHint(rules) ? (
                <p className="mt-1.5 font-body text-[12.5px] leading-[1.5] text-faint">{rulesHint(rules)}</p>
              ) : null
            ) : (
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

          <Primary
            disabled={
              busy || !email.trim() || !password || (making && password.length < (rules?.min_length || 8))
            }
          >
            {busy ? 'One moment' : making ? 'Create account' : 'Sign in'}
          </Primary>
        </form>

        <button
          type="button"
          disabled={busy || !email.trim()}
          onClick={() => run(() => sendEmailCode(email).then(after))}
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
