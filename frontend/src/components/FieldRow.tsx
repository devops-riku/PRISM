import InfoHint from './InfoHint'
import type { ReactNode } from 'react'

/**
 * The pad's field label.
 *
 * This file used to hold `FieldRow` as well — a 56px numbered rail down the
 * left of every field, from when the pad was one long numbered column. The pad
 * is stepped now and the numbers went with it: a step rail that already says
 * where you are does not need each field counting itself as well.
 */

type FieldLabelProps = {
  htmlFor: string
  children: ReactNode
  /**
   * The guidance that used to sit under the label as its own paragraph.
   *
   * Passed here rather than rendered by each caller so every field in the app
   * puts its ⓘ in the same place — immediately after the label text, on the
   * label's own line. A hint that moves around is a hint people stop looking
   * for.
   *
   * The icon is OUTSIDE the `<label>` element on purpose. A `<button>` inside
   * a `<label>` is one of the few nestings browsers actively punish: clicking
   * it would also activate the control the label points at, so opening the
   * hint would focus the textarea underneath — and on a checkbox it would
   * toggle the box.
   */
  info?: ReactNode
  /**
   * What to call this field in the icon's accessible name, when `children`
   * is not a plain string.
   *
   * Several labels interpolate — `Target cost ({currency}, {taxNote})` is an
   * array, not a string — and the `typeof children === 'string'` check below
   * would quietly fall back to "About this field" for every one of them.
   * That is precisely the announcement `InfoHint`'s own docstring rules out,
   * and it fails silently: the screen looks right and only a screen reader
   * hears the difference.
   */
  infoLabel?: string
}

/** Small caption above a control. */
export function FieldLabel({ htmlFor, children, info, infoLabel }: FieldLabelProps) {
  const label = (
    <label
      htmlFor={htmlFor}
      className="block font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void"
    >
      {children}
    </label>
  )

  // `mb-2` stays on the wrapper rather than the label, so a field with a hint
  // and a field without one leave exactly the same gap above their control.
  if (!info) return <div className="mb-2">{label}</div>

  return (
    <div className="mb-2 flex items-center gap-1.5">
      {label}
      <InfoHint label={infoLabel || (typeof children === 'string' ? children : 'this field')}>
        {info}
      </InfoHint>
    </div>
  )
}
