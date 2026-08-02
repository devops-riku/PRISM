/**
 * Where "back" actually goes.
 *
 * The close button used to be a link to the front page, which is wrong
 * everywhere except the one case where somebody happened to arrive from it.
 * Open a quotation from the list, close it, and you were on the home screen
 * rather than back in the list you were reading.
 *
 * `history.back()` alone is not the fix either: a quotation opened from a
 * bookmark, an email, or a new tab has nothing of ours behind it, and going
 * back would leave the app entirely — from a close button, that reads as the
 * page crashing.
 *
 * So each history entry is stamped with its position as we arrive at it. The
 * entry the app loaded into is 0; anything above that has one of ours behind it
 * and can be closed by going back. An entry revisited by the browser's own Back
 * or Forward keeps the number it was first given, because `replaceState` writes
 * the state onto the entry rather than into this module.
 */

const KEY = 'prismIdx'

let position = 0

function stamp(): void {
  // `history.state` is whatever was put there, so it is read as an unknown
  // bag and the stamp is checked for before it is trusted.
  const state: Record<string, unknown> | null = window.history.state
  const known = state && typeof state[KEY] === 'number' ? state[KEY] : null

  if (known !== null) {
    // Somewhere we have already been — the browser handed the entry back with
    // its number on it.
    position = known
    return
  }

  position += 1
  try {
    window.history.replaceState({ ...(state || {}), [KEY]: position }, '')
  } catch {
    // Private modes and a few embedded browsers refuse replaceState. Nothing
    // here is worth breaking navigation over; `canGoBack` just gets stricter.
  }
}

// The entry the app loaded into. Counted as 0 whether or not it can be
// stamped, so a first page with a bookmark behind it never reports a way back.
if (typeof window !== 'undefined') {
  const state: Record<string, unknown> | null = window.history.state
  position = state && typeof state[KEY] === 'number' ? state[KEY] : 0
  if (!(state && typeof state[KEY] === 'number')) {
    try {
      window.history.replaceState({ ...(state || {}), [KEY]: 0 }, '')
    } catch {
      /* see above */
    }
  }
  window.addEventListener('hashchange', stamp)
}

/**
 * Tell this module about a navigation it could not see.
 *
 * `pushState` and `replaceState` do not fire `hashchange`, so a screen that
 * changes the URL itself — the pad, which pushes the quotation it just prepared
 * — has to say so, or its page looks like the one the app loaded into and its
 * close button goes home instead of back to the pad.
 *
 * @param how  `replace` for an entry rewritten in place.
 */
export function noteNavigation({ replace = false }: { replace?: boolean } = {}): void {
  if (!replace) {
    stamp()
    return
  }
  // Same entry, new URL: keep its number, and put it back on — `replaceState`
  // overwrites the whole state object, stamp included.
  try {
    window.history.replaceState({ ...(window.history.state || {}), [KEY]: position }, '')
  } catch {
    /* see stamp() */
  }
}

/** True when there is a page of ours behind this one. */
export function canGoBack(): boolean {
  return position > 0
}

/**
 * Close this screen: back to wherever it was opened from, or to `fallback` when
 * it was opened directly.
 *
 * @param fallback  a hash href, e.g. '#/'
 */
export function goBack(fallback: string = '#/'): void {
  if (canGoBack()) {
    window.history.back()
    return
  }
  window.location.hash = fallback
}
