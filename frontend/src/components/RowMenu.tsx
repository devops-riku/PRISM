import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react'

/**
 * The actions on a row, behind one button.
 *
 * Every row of the quotations list carried its own PDF and Delete buttons — two
 * controls per row, repeated down the page, so a list of twenty quotations was
 * forty buttons competing with the thing you came to read. Worse, Delete sat
 * permanently one careless click from a quotation you cannot get back.
 *
 * One quiet target per row now, opened deliberately. Destructive items are
 * marked and sit last, after a divider, so the pointer has to travel past
 * everything safe to reach them.
 *
 * `items` are `{ label, href, onSelect, danger }`. An `href` renders a link so
 * downloads and new tabs keep working; anything else is a button.
 */

export type RowMenuItem = {
  label: string
  href?: string
  onSelect?: () => void
  danger?: boolean
}

type RowMenuProps = {
  items?: RowMenuItem[]
  label?: string
}

export default function RowMenu({ items = [], label = 'Actions for this row' }: RowMenuProps) {
  const safe = items.filter((item) => !item.danger)
  const dangerous = items.filter((item) => item.danger)

  const render = (item: RowMenuItem) => {
    const classes = `block w-full cursor-pointer rounded-xs px-3 py-2 text-left font-body text-[14px] no-underline data-[focus]:bg-duplicate ${
      item.danger ? 'text-alert data-[focus]:bg-alert-soft' : 'text-body'
    }`
    return (
      <MenuItem key={item.label}>
        {item.href ? (
          <a href={item.href} className={classes}>
            {item.label}
          </a>
        ) : (
          <button type="button" onClick={item.onSelect} className={classes}>
            {item.label}
          </button>
        )}
      </MenuItem>
    )
  }

  return (
    <Menu>
      <MenuButton
        aria-label={label}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-transparent text-void transition-[color,border-color,background-color] duration-150 hover:border-rule hover:bg-duplicate hover:text-ink active:bg-rule data-[open]:border-rule data-[open]:bg-duplicate data-[open]:text-ink"
      >
        {/* Three dots, drawn. A glyph would inherit the body font's baseline
            and sit off-centre in a square button. */}
        <svg aria-hidden="true" viewBox="0 0 4 16" className="h-4 w-1" fill="currentColor">
          <circle cx="2" cy="2" r="1.6" />
          <circle cx="2" cy="8" r="1.6" />
          <circle cx="2" cy="14" r="1.6" />
        </svg>
      </MenuButton>

      <MenuItems
        anchor="bottom end"
        // Two Headless UI defaults, both off on purpose.
        //
        // `modal` locks the document while the menu is open and compensates for
        // the scrollbar by adding padding-right to <html>. On a centred layout
        // that padding shoves the whole card sideways the moment the menu
        // opens. Nothing here needs the lock: every screen pins its own
        // scrolling already.
        //
        // `transition` faded the panel in over 150ms - long enough to watch it
        // being positioned. The panel mounts before floating-ui has measured
        // the button, so the first painted frame sits at the container's left
        // edge and the correction reads as a slide across the screen. Appearing
        // in place is both correct and quieter.
        modal={false}
        className="z-50 min-w-[11rem] rounded-lg border border-rule bg-paper p-1 shadow-raised [--anchor-gap:4px] focus:outline-none"
      >
        {safe.map(render)}
        {dangerous.length && safe.length ? (
          <div role="presentation" className="my-1 h-px bg-hairline" />
        ) : null}
        {dangerous.map(render)}
      </MenuItems>
    </Menu>
  )
}
