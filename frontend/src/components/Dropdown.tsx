import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from '@headlessui/react'
import type { ListboxButtonProps } from '@headlessui/react'

/**
 * A select that belongs to this design rather than to the operating system.
 *
 * A native `<select>` cannot be styled where it matters: the popup is drawn by
 * the OS, in the OS's font, at the OS's row height. Next to fields wearing the
 * kit's 12px radius and warm paper fill, it read as a control borrowed from
 * somewhere else — which is exactly what it was.
 *
 * Headless UI supplies the behaviour and nothing else: focus management, type
 * to select, arrow keys, escape to close, and the ARIA a listbox needs. The
 * appearance below is the kit's, the same `.well` every input already wears.
 *
 * Options are `{ value, label, hint }`. `hint` is a second line for the cases
 * where the choice needs explaining in the list rather than underneath it.
 *
 * Anything else passed - `aria-label` above all - lands on the button. A
 * `<label htmlFor>` still works because a button is a labelable element, but
 * the rows of a table have no room for visible labels and need the attribute:
 * six unit dropdowns all announcing themselves as "day" name nothing.
 */

/**
 * One row of the list. `T` is the value the field round-trips: a union of
 * literals at every call site, and constrained to `string` because a value is
 * also the option's React key.
 */
export type DropdownOption<T extends string = string> = {
  value: T
  label: string
  hint?: string
}

type DropdownOwnProps<T extends string> = {
  id?: string
  value: T
  onChange: (value: T) => void
  options?: DropdownOption<T>[]
  disabled?: boolean
  placeholder?: string
  className?: string
  buttonClassName?: string
}

/** The rest is whatever a Headless UI listbox button takes, `aria-label` above all. */
type DropdownProps<T extends string> = DropdownOwnProps<T> &
  Omit<ListboxButtonProps<'button'>, keyof DropdownOwnProps<string>>

export default function Dropdown<T extends string = string>({
  id,
  value,
  onChange,
  options = [],
  disabled = false,
  placeholder = 'Choose',
  className = '',
  buttonClassName = '',
  ...rest
}: DropdownProps<T>) {
  const selected = options.find((option) => option.value === value)

  return (
    <Listbox value={value} onChange={onChange} disabled={disabled}>
      <div className={`relative ${className}`}>
        <ListboxButton
          id={id}
          {...rest}
          className={`well flex w-full items-center justify-between gap-3 text-left data-[disabled]:cursor-not-allowed data-[disabled]:text-faint ${buttonClassName}`}
        >
          <span className={`truncate ${selected ? '' : 'text-faint'}`}>
            {selected ? selected.label : placeholder}
          </span>
          {/* Drawn, not typed: no font on Windows carries a chevron that sits
              on the baseline the way this needs to. */}
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
        </ListboxButton>

        <ListboxOptions
          anchor="bottom start"
          // Both defaults off, for the reasons written out in RowMenu: modal
          // pads <html> and slides the page, and the fade makes the panel's
          // pre-measurement position visible as a slide.
          modal={false}
          className="z-50 w-[var(--button-width)] min-w-[12rem] rounded-lg border border-rule bg-paper p-1 shadow-raised [--anchor-gap:6px] focus:outline-none"
        >
          {options.map((option) => (
            <ListboxOption
              key={option.value}
              value={option.value}
              className="cursor-pointer rounded-xs px-3 py-2 font-body text-[14px] text-body data-[focus]:bg-duplicate data-[selected]:font-medium data-[selected]:text-ink"
            >
              <span className="block truncate">{option.label}</span>
              {option.hint ? (
                <span className="mt-0.5 block truncate font-label text-[12px] text-faint">
                  {option.hint}
                </span>
              ) : null}
            </ListboxOption>
          ))}
        </ListboxOptions>
      </div>
    </Listbox>
  )
}
