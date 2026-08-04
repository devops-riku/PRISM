/**
 * Which palette the studio is in, and remembering it.
 *
 * Dark is the default and stays it: the app was designed dark, and an install
 * with no stored preference should open the way the screenshots do.
 *
 * The switch is one attribute on <html>, because that is where the dark values
 * live - index.css selects `html[data-theme='light']` to swap them for the
 * same block `.sheet-light` uses. Nothing else in the app knows a theme
 * exists, which is the property that made the re-skin a palette rather than 54
 * files.
 */

export type Theme = 'dark' | 'light'

const KEY = 'prism.theme'

/** The stored choice, or dark. Never throws: a browser with storage disabled -
 *  Safari's private mode, a locked-down profile - must still render an app. */
export function readTheme(): Theme {
  try {
    return window.localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

/** Apply and remember.
 *
 *  The attribute is REMOVED rather than set to 'dark', so the default is the
 *  absence of a choice: the CSS carries one selector instead of two, and an
 *  install that has never touched this renders the same as one that switched
 *  to light and back - even though the two are still distinguishable in raw
 *  storage. */
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
