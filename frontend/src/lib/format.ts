import type { ChangeEvent } from 'react'

/**
 * Presentation helpers.
 *
 * Two rules govern this file.
 *
 * 1. **No conversion, ever.** Every amount is already denominated in the
 *    currency it is passed with — Gemini priced the line items directly in the
 *    requested currency for the requested market. There is no rate table here
 *    and there must never be one. These functions change how a number *looks*,
 *    never what it is worth.
 *
 * 2. **The locale is pinned.** The same page shows figures formatted here next
 *    to figures baked into server-rendered markdown. If the client followed the
 *    visitor's locale, a German browser would print "1.234,50" beside the
 *    server's "1,234.50" in the same table. So everything below formats in
 *    `en-US`, which is what the server's `format_money` produces.
 */

const LOCALE = 'en-US'

/**
 * ISO 4217 currencies with no minor unit. `Intl.NumberFormat` already knows
 * these, but stating them explicitly means the fallback path (an unknown code,
 * or an engine with a thin ICU build) rounds the same way the happy path does.
 */
const ZERO_DECIMAL = new Set([
  'BIF', 'CLP', 'DJF', 'GNF', 'ISK', 'JPY', 'KMF', 'KRW', 'MGA',
  'PYG', 'RWF', 'UGX', 'VND', 'VUV', 'XAF', 'XOF', 'XPF',
])

const ISO_4217 = /^[A-Z]{3}$/

/** `Intl.NumberFormat` construction is not cheap; these are hot in tables. */
const moneyFormatters = new Map<string, Intl.NumberFormat | null>()
const numberFormatters = new Map<string, Intl.NumberFormat>()

/**
 * Coerce anything the API might hand us into a finite number.
 * Money fields are plain floats, but a partial model response can leave a null
 * or a string in place, and a table full of "NaN" is worse than a table of
 * zeroes.
 */
function toFiniteNumber(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return 0
  // -0 formats as "-$0.00", which reads as a mistake.
  return Object.is(n, -0) ? 0 : n
}

/**
 * @returns an upper-case ISO 4217 code, or '' if it is not one
 */
function normaliseCode(code: unknown): string {
  const upper = String(code ?? '')
    .trim()
    .toUpperCase()
  return ISO_4217.test(upper) ? upper : ''
}

/**
 * @param code a validated ISO 4217 code
 * @returns null when the runtime rejects the code
 */
function moneyFormatter(code: string): Intl.NumberFormat | null {
  // `has` has already proved there is an entry; the `?? null` only satisfies
  // `Map.get`'s `| undefined`, and a stored entry is a formatter or null.
  if (moneyFormatters.has(code)) return moneyFormatters.get(code) ?? null

  const digits = ZERO_DECIMAL.has(code) ? 0 : 2
  let formatter: Intl.NumberFormat | null = null
  try {
    formatter = new Intl.NumberFormat(LOCALE, {
      style: 'currency',
      currency: code,
      // 'symbol' rather than 'narrowSymbol': it keeps A$ / C$ / S$ distinct,
      // which matters when the market region is the whole point.
      currencyDisplay: 'symbol',
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })
  } catch {
    // Intl accepts any well-formed three-letter code — an unrecognised one is
    // simply printed as its own symbol. It throws RangeError only on a
    // *malformed* code, which is what this catch is for.
    formatter = null
  }

  moneyFormatters.set(code, formatter)
  return formatter
}

/**
 * Format an amount in its own currency.
 *
 *   formatMoney(1234.5, 'PHP')  -> '₱1,234.50'
 *   formatMoney(1234.5, 'JPY')  -> '¥1,235'       (no minor unit)
 *   formatMoney(1234.5, 'XYZ')  -> 'XYZ 1,234.50' (unrecognised code prints as
 *                                                  its own symbol, via Intl)
 *   formatMoney(1234.5, 'peso') -> 'peso 1,234.50' (malformed code, still
 *                                                   labelled — an unlabelled
 *                                                   figure is the one thing a
 *                                                   quotation must never show)
 *
 * @param amount already denominated in `currency`
 * @param currency ISO 4217 code
 */
export function formatMoney(amount: number, currency: string = 'PHP'): string {
  const value = toFiniteNumber(amount)
  const code = normaliseCode(currency)

  if (code) {
    const formatter = moneyFormatter(code)
    if (formatter) return formatter.format(value)
  }

  // Unknown or malformed code: show the figure with the raw code in front of
  // it rather than dropping the currency entirely. An unlabelled number is the
  // one thing a quotation must never contain.
  const digits = ZERO_DECIMAL.has(code) ? 0 : 2
  const figure = formatNumber(value, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  const label = code || String(currency ?? '').trim()
  return label ? `${label} ${figure}` : figure
}

/**
 * Format a bare number — quantities, durations, counts.
 * Defaults drop trailing zeroes, so 40 prints as "40" and 12.5 as "12.5".
 */
export function formatNumber(value: number, options: Intl.NumberFormatOptions = {}): string {
  const n = toFiniteNumber(value)
  const resolved = {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
    ...options,
  }

  const key = JSON.stringify(resolved)
  let formatter = numberFormatters.get(key)
  if (!formatter) {
    try {
      formatter = new Intl.NumberFormat(LOCALE, resolved)
    } catch {
      // Bad options from a caller should not take the table down.
      formatter = new Intl.NumberFormat(LOCALE)
    }
    numberFormatters.set(key, formatter)
  }

  return formatter.format(n)
}

/**
 * Format a percentage.
 *
 * The value is already a percentage, not a ratio — `CostSummary.contingency_pct`
 * of 10 means 10%. This deliberately does not use `style: 'percent'`, which
 * would multiply by a hundred and print "1,000%".
 *
 *   formatPct(10)    -> '10%'
 *   formatPct(12.5)  -> '12.5%'
 *
 * @param value a percentage, e.g. 10 for 10%
 */
export function formatPct(value: number, options: { maximumFractionDigits?: number } = {}): string {
  const { maximumFractionDigits = 2 } = options
  return `${formatNumber(value, { maximumFractionDigits })}%`
}

/**
 * A bare ISO datetime with no timezone designator. Python's
 * `datetime.utcnow().isoformat()` produces exactly this, and JavaScript parses
 * it as *local* time — which silently shifts every timestamp by the visitor's
 * UTC offset. The contract says `created_at` is UTC, so we say so explicitly.
 */
const NAKED_ISO = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/

function toDate(value: string | number | Date): Date | null {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  if (typeof value === 'number') {
    const fromEpoch = new Date(value)
    return Number.isNaN(fromEpoch.getTime()) ? null : fromEpoch
  }

  const text = String(value ?? '').trim()
  if (!text) return null

  const normalised = NAKED_ISO.test(text) ? `${text.replace(' ', 'T')}Z` : text
  const parsed = new Date(normalised)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

const dateFormatter = new Intl.DateTimeFormat(LOCALE, {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

const dateTimeFormatter = new Intl.DateTimeFormat(LOCALE, {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/**
 * Format a timestamp for the quotation header, in the reader's own timezone.
 *
 *   formatDate('2026-07-31T04:15:00Z')                  -> 'Jul 31, 2026'
 *   formatDate('2026-07-31T04:15:00Z', { withTime: 1 }) -> 'Jul 31, 2026, 12:15'
 *
 * Returns '' for anything unparseable, so a missing timestamp renders as
 * nothing rather than as "Invalid Date".
 *
 * @param value ISO-8601 string, epoch ms, or a Date
 */
export function formatDate(
  value: string | number | Date,
  options: { withTime?: boolean } = {},
): string {
  const date = toDate(value)
  if (!date) return ''
  return options.withTime
    ? dateTimeFormatter.format(date)
    : dateFormatter.format(date)
}

/**
 * How long something has been running, said the way a person waiting says it.
 *
 *   elapsedLabel(8)    -> '8s'
 *   elapsedLabel(95)   -> '1m 35s'
 *
 * Shared by the pad's strip and the In progress page, which show the same wait
 * in two places and must not word it two ways.
 */
export function elapsedLabel(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds || 0))
  if (whole < 60) return `${whole}s`
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, '0')}s`
}

/**
 * Seconds between a server timestamp and now.
 *
 * Clamped at zero on purpose. The timestamp is the API's clock and `now` is the
 * browser's; a machine a few seconds behind the server would otherwise report a
 * job that has been running for negative eleven seconds.
 *
 * @param now  epoch milliseconds
 */
export function secondsSince(iso: string, now: number): number {
  const started = Date.parse(iso || '')
  if (!Number.isFinite(started)) return 0
  return Math.max(0, Math.floor((now - started) / 1000))
}

// --- typed money fields ------------------------------------------------------

/**
 * Group the digits of a part-typed amount, leaving everything else alone.
 *
 *   groupDigits('3000000')   -> '3,000,000'
 *   groupDigits('3000000.')  -> '3,000,000.'      (mid-typing, keep the point)
 *   groupDigits('₱1234.5')   -> '1,234.5'
 *   groupDigits('')          -> ''
 *
 * Deliberately lenient: it is called on every keystroke, so a value that is not
 * yet a number has to survive being formatted. Only the integer part is
 * grouped, the decimal part is capped at two digits, and a lone trailing point
 * is preserved so "3000000." does not fight the person typing "3000000.50".
 */
export function groupDigits(raw: string | number): string {
  const cleaned = String(raw ?? '').replace(/[^\d.]/g, '')
  if (!cleaned) return ''

  const [whole, ...rest] = cleaned.split('.')
  const grouped = (whole || '').replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  if (rest.length === 0) return grouped

  const decimals = rest.join('').slice(0, 2)
  return `${grouped || '0'}.${decimals}`
}

/**
 * Strip the grouping back out, for anything that has to do arithmetic.
 *
 *   ungroupDigits('3,000,000.50') -> '3000000.50'
 */
export function ungroupDigits(raw: string | number): string {
  return String(raw ?? '').replace(/[^\d.]/g, '')
}

/**
 * An onChange handler for a money field that groups as you type.
 *
 * Inserting commas moves every character after them, so the caret has to be put
 * back or typing in the middle of a figure jumps to the end on the third digit.
 * It is restored by counting significant characters — digits and the decimal
 * point — rather than raw offsets, since those are exactly what survive
 * reformatting.
 *
 *   <input value={target} onChange={groupedInputHandler(setTarget)} />
 */
export function groupedInputHandler(
  setValue: (value: string) => void,
): (event: ChangeEvent<HTMLInputElement>) => void {
  return (event) => {
    const input = event.target
    const typed = input.value
    const caret = input.selectionStart == null ? typed.length : input.selectionStart
    const significantBefore = ungroupDigits(typed.slice(0, caret)).length

    const next = groupDigits(typed)
    setValue(next)

    // React rewrites the value after this returns, so the caret is placed on
    // the next frame. A field that has since lost focus is left alone.
    window.requestAnimationFrame(() => {
      if (document.activeElement !== input) return

      let seen = 0
      let position = next.length
      if (significantBefore === 0) {
        position = 0
      } else {
        for (let i = 0; i < next.length; i += 1) {
          if (/[\d.]/.test(next[i])) seen += 1
          if (seen === significantBefore) {
            position = i + 1
            break
          }
        }
      }
      input.setSelectionRange(position, position)
    })
  }
}
