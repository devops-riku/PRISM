/**
 * Shared class strings for the Clarity Kit direction (docs/DESIGN.md).
 *
 * The design system lives in src/index.css: the kit's palette, its two
 * typefaces, its stepped radii, its four shadows, and the hand-written
 * component classes below. This file only names them, so there is exactly one
 * definition of "a field" or "an eyebrow" in the client.
 *
 * Written as complete literals so the Tailwind v4 scanner can see every class.
 */

// Figtree 600 with tight tracking. Sentence case — the kit saves
// capitals for the small labels below.
export const DISPLAY = 'display'

// The kit's eyebrow: 12px, uppercase, 0.14em tracking, faint.
export const MONO_LABEL =
  'font-label text-[12px] font-medium uppercase tracking-[0.14em] text-faint'

// A figure meant to be read as a statement rather than scanned in a column:
// Figtree at 600 with the kit's tight -0.03em tracking, the treatment it
// gives its own headline numbers. Tabular, so it still aligns if it ever lands
// in a column.
export const FIGURE = 'figure text-ink'

/**
 * The kit's field: surface fill, 1px border, 12px radius, and on focus an
 * accent border plus a 4px tinted ring. `.well` owns the focus treatment — do
 * not override its border, padding or radius from here.
 */
export const WELL = 'well'

/**
 * The brief: the one question the whole product hangs on, so it is set larger
 * than the other fields and given room. `.well-ruled` pins its own font size
 * and line height.
 */
export const WELL_TEXTAREA = 'well well-ruled min-h-[184px]'

// The primary action: solid accent, soft green shadow, 11px radius. Same height
// and minimum width as `ACTION` - the fill is the whole difference, because a
// button that is also wider and taller than the one beside it stops reading as
// the same kind of control.
export const ACTION_PRIMARY = 'action-primary'

// Secondary action: download, print, remove, delete. Outlined, never filled.
export const ACTION = 'action-quiet'

// A card: surface on canvas, hairline border, 14px radius, the kit's soft
// shadow. Section headers sit above a 1px rule inside it.
export const CARD = 'rounded-xl border border-rule bg-paper shadow-sheet'
