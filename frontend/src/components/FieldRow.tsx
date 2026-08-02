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
}

/** Small caption above a control. */
export function FieldLabel({ htmlFor, children }: FieldLabelProps) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-2 block font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void"
    >
      {children}
    </label>
  )
}
