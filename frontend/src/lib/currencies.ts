import type { Currency } from '../types'

/**
 * Currency fallback list.
 *
 * `GET /api/currencies` is authoritative — the server decides which markets
 * PRISM will price for. This list exists so the currency select renders with
 * real options on the very first paint, before that request resolves (and
 * still works if the API is not running yet). `fetchCurrencies()` in ./api.js
 * falls back to exactly this array.
 *
 * PHP is first and is the default: PRISM is built in Manila and prices in
 * Philippine pesos unless told otherwise.
 *
 * Note there is no exchange rate here, and there must never be one. Gemini
 * prices line items directly in the requested currency for the requested
 * market; nothing in this system converts between currencies.
 */

export const CURRENCIES: ReadonlyArray<Currency> = Object.freeze([
  { code: 'PHP', name: 'Philippine peso', symbol: '₱' },
  { code: 'USD', name: 'US dollar', symbol: '$' },
  { code: 'EUR', name: 'Euro', symbol: '€' },
  { code: 'GBP', name: 'Pound sterling', symbol: '£' },
  { code: 'AUD', name: 'Australian dollar', symbol: 'A$' },
  { code: 'CAD', name: 'Canadian dollar', symbol: 'C$' },
  { code: 'SGD', name: 'Singapore dollar', symbol: 'S$' },
  { code: 'HKD', name: 'Hong Kong dollar', symbol: 'HK$' },
  { code: 'NZD', name: 'New Zealand dollar', symbol: 'NZ$' },
  { code: 'JPY', name: 'Japanese yen', symbol: '¥' },
  { code: 'CNY', name: 'Chinese yuan', symbol: '¥' },
  { code: 'KRW', name: 'South Korean won', symbol: '₩' },
  { code: 'INR', name: 'Indian rupee', symbol: '₹' },
  { code: 'IDR', name: 'Indonesian rupiah', symbol: 'Rp' },
  { code: 'MYR', name: 'Malaysian ringgit', symbol: 'RM' },
  { code: 'THB', name: 'Thai baht', symbol: '฿' },
  { code: 'VND', name: 'Vietnamese dong', symbol: '₫' },
  { code: 'AED', name: 'UAE dirham', symbol: 'AED' },
  { code: 'SAR', name: 'Saudi riyal', symbol: 'SAR' },
  { code: 'CHF', name: 'Swiss franc', symbol: 'CHF' },
  { code: 'ZAR', name: 'South African rand', symbol: 'R' },
  { code: 'BRL', name: 'Brazilian real', symbol: 'R$' },
])

/** The currency the form starts on. */
export const DEFAULT_CURRENCY = 'PHP'

/**
 * Look a currency up by ISO 4217 code, case-insensitively.
 *
 * Returns `null` rather than throwing, because the code can come straight from
 * a server response — an unrecognised one should degrade to showing the bare
 * code, not break the render.
 *
 * @param list defaults to the built-in list
 */
export function findCurrency(code: string, list: ReadonlyArray<Currency> = CURRENCIES): Currency | null {
  const wanted = String(code ?? '')
    .trim()
    .toUpperCase()
  if (!wanted) return null
  for (const entry of list) {
    if (String(entry?.code ?? '').toUpperCase() === wanted) return entry
  }
  return null
}
