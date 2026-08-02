import { useEffect, useMemo, useState } from 'react'
import { fetchCurrencies } from '../lib/api'
import { CURRENCIES } from '../lib/currencies'
import Dropdown from './Dropdown'
import type { Currency } from '../types'

/**
 * An entry as it arrives, before `normalise` has decided whether it is one.
 * Every field is `unknown` because that is the whole point of the function
 * below: a `Currency` is what comes out of it, not what goes in.
 */
type CurrencyLike = { code?: unknown; name?: unknown; symbol?: unknown }

function normalise(list: unknown): Currency[] {
  if (!Array.isArray(list)) return []
  const seen = new Set<string>()
  const out: Currency[] = []
  list.forEach((entry: CurrencyLike) => {
    const code = String((entry && entry.code) || entry || '')
      .trim()
      .toUpperCase()
    if (!/^[A-Z]{3}$/.test(code) || seen.has(code)) return
    seen.add(code)
    out.push({
      code,
      name: String((entry && entry.name) || '').trim(),
      symbol: String((entry && entry.symbol) || '').trim(),
    })
  })
  return out
}

type CurrencySelectProps = {
  id?: string
  value: string
  onChange: (value: string) => void
  disabled?: boolean
}

/**
 * Line items are priced directly in this currency — there is no FX conversion
 * anywhere in PRISM, so the choice made here is the choice the model prices in.
 * The bundled list renders immediately; the server list replaces it if it
 * arrives, and the field keeps working if it never does.
 */
export default function CurrencySelect({
  id,
  value,
  onChange,
  disabled = false,
}: CurrencySelectProps) {
  const [options, setOptions] = useState(() => normalise(CURRENCIES))

  useEffect(() => {
    let live = true
    Promise.resolve()
      .then(() => fetchCurrencies())
      .then((list) => {
        const next = normalise(list)
        if (live && next.length > 0) setOptions(next)
      })
      .catch(() => {
        /* The bundled list is a complete fallback; a dead endpoint is not a
           reason to break the form. */
      })
    return () => {
      live = false
    }
  }, [])

  const resolved = useMemo(() => {
    if (options.some((option) => option.code === value)) return options
    return [{ code: value, name: '', symbol: '' }, ...options]
  }, [options, value])

  return (
    <Dropdown
      id={id}
      value={value}
      disabled={disabled}
      onChange={onChange}
      buttonClassName="font-label text-[13px]"
      options={resolved.map((option) => ({
        value: option.code,
        label: option.symbol ? `${option.code}  ${option.symbol}` : option.code,
        hint: option.name || '',
      }))}
    />
  )
}
