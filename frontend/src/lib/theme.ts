/**
 * Which palette the studio is in, and remembering it.
 *
 * LIGHT IS THE DEFAULT. It was dark, on the reasoning that the app was
 * designed dark and an install with no stored preference should open the way
 * the screenshots do. The studio asked for the opposite, so an install that
 * has never touched this now opens light.
 *
 * `data-theme='light'` STILL MEANS LIGHT, which is worth stating because the
 * obvious way to make light the default is to invert the attribute and let
 * absence mean light. That is not what happened here. `index.css` carries the
 * dark values in `@theme` and selects `html[data-theme='light']` to swap them
 * - and the very same block is what `.sheet-light` uses to paint a client's
 * page and a design preview inside a dark app. Inverting the attribute would
 * mean inverting that block too, which is a re-write of the palette rather
 * than a change of default.
 *
 * So the default moved and the vocabulary did not: the attribute is now
 * PRESENT unless the studio has explicitly chosen dark. The consequence is
 * that the default state is no longer the absence of a choice - it is written
 * onto <html> by the script in index.html before first paint, and if that
 * script never runs the app falls back to dark. That is the trade, and it is
 * the reason the script is in <head> rather than at the end of <body>.
 *
 * Nothing else in the app knows a theme exists, which is the property that
 * made the re-skin a palette rather than 54 files.
 */

export type Theme = 'dark' | 'light'

const KEY = 'prism.theme'

/** The stored choice, or light. Never throws: a browser with storage disabled
 *  - Safari's private mode, a locked-down profile - must still render an app,
 *  and it gets the default like any other first visit. */
export function readTheme(): Theme {
  try {
    return window.localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/** Apply and remember.
 *
 *  Both branches are explicit now. While dark was the default this removed the
 *  attribute rather than setting it, so the CSS needed one selector instead of
 *  two; with light as the default the attribute has to be written for the
 *  common case anyway, and `removeAttribute` for dark keeps the dark palette
 *  as the one `@theme` states directly. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'light') root.setAttribute('data-theme', 'light')
  else root.removeAttribute('data-theme')
  try {
    window.localStorage.setItem(KEY, theme)
  } catch {
    // A studio that cannot store its preference still gets to use it for this
    // session. Refusing to switch because the choice cannot be remembered
    // would be the worse failure.
  }
}

export function toggleTheme(): Theme {
  // The DOM, not storage. `applyTheme` sets the attribute outside its
  // try/catch, so it is correct even when the write that follows it fails -
  // and on a browser where writes never persist, asking storage what the
  // current theme is returns the pre-failure answer for ever and this button
  // stops doing anything after one press. The module already promises to
  // survive that case; reading storage here is what broke the promise.
  const current: Theme =
    document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
  const next: Theme = current === 'light' ? 'dark' : 'light'
  applyTheme(next)
  return next
}
