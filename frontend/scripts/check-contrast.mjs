/**
 * Every colour pair in the theme, measured against WCAG AA.
 *
 * Dependency-free on purpose: it parses the same `index.css` the app ships and
 * needs no browser and no install, so it runs in CI, on a fresh clone, and in
 * the two seconds after someone edits a hex by hand.
 *
 * Run: node scripts/check-contrast.mjs   (from frontend/)
 */
import { readFileSync } from 'node:fs'

const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')

/** The `@theme` block's values - the dark palette, which is the default. */
function tokens(scope) {
  let body
  if (scope === 'dark') {
    body = css.slice(css.indexOf('@theme'), css.indexOf('}', css.indexOf('--color-alert-soft')))
  } else {
    // Anchored to the RULE's own selector line, not a bare substring match:
    // the dark palette's doc comment above --color-canvas explains
    // `.sheet-light` in prose (backtick-quoted - `` `.sheet-light` below is
    // how... ``), and a plain `indexOf('.sheet-light')` finds that mention
    // first - it sits earlier in the file than any actual `.sheet-light`
    // rule ever will. Required at the START of a line (only whitespace
    // before it) so the backtick-prefixed comment text can never match, and
    // followed by `,` or `{` (not a backtick) so it works whether the rule
    // is its own selector (`.sheet-light {`) or paired with the theme
    // toggle in a selector list (`.sheet-light,\nhtml[data-theme=light] {`,
    // the shape the plan's later light-mode toggle task calls for) - the
    // brace that actually opens the rule is then found by scanning forward
    // from wherever the selector line matched, which is correct either way.
    const selector = css.match(/^[ \t]*\.sheet-light[ \t]*[,{]/m)
    const start = selector ? css.indexOf('{', selector.index) : -1
    body = start === -1 ? '' : css.slice(start, css.indexOf('}', start))
  }
  const found = {}
  for (const [, name, hex] of body.matchAll(/--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    found[name] = hex
  }
  // `--well-border` is not a `--color-*` token, so it needs its own lookup -
  // but it still has to come from THIS scope's own body. Resolving it once,
  // file-globally, always finds the dark rule's value (whichever comes first
  // in the file) and measures the light input-border pair against a colour
  // that was never actually on screen.
  const wellBorder = body.match(/--well-border:\s*(#[0-9a-fA-F]{6})/)
  if (wellBorder) found['well-border'] = wellBorder[1]
  return found
}

const luminance = (hex) => {
  const raw = hex.replace('#', '')
  const channels = [0, 2, 4].map((i) => parseInt(raw.slice(i, i + 2), 16) / 255)
  const linear = channels.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}

const ratio = (a, b) => {
  const [x, y] = [luminance(a), luminance(b)]
  const [hi, lo] = x > y ? [x, y] : [y, x]
  return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100
}

/** `[label, foreground token, background token, floor]`. */
const PAIRS = [
  ['headings on a card', 'ink', 'paper', 4.5],
  ['body on a card', 'body', 'paper', 4.5],
  ['secondary on a card', 'void', 'paper', 4.5],
  ['captions on a card', 'faint', 'paper', 4.5],
  ['captions on the page', 'faint', 'canvas', 4.5],
  ['body on the page', 'body', 'canvas', 4.5],
  ['the accent on a card', 'ballpoint', 'paper', 4.5],
  ['the accent on the page', 'ballpoint', 'canvas', 4.5],
  ['the accent on its own tint', 'ballpoint', 'accent-soft', 4.5],
  ['alert on a card', 'alert', 'paper', 4.5],
  ['body on a well', 'body', 'duplicate', 4.5],
]

// An input's edge has to be findable. It is the one non-text pair that
// matters: a divider nobody sees is a style, a field nobody can find is a
// fault. `well-border` is not a `--color-*` token, so it is named separately
// from PAIRS, but it resolves through the same per-scope `tokens()` and is
// measured against that SAME scope's own `paper`/`canvas` - not always
// dark's, which is the bug this pair used to carry.
const WELL_BORDER_PAIRS = [
  ['input border on a card', 'well-border', 'paper', 3.0],
  ['input border on the page', 'well-border', 'canvas', 3.0],
]

// Every token any pair names, derived rather than hand-listed a second time -
// a hand-written list is the same "two places that drift" mistake this file
// already tells the story of once (`--well-border`, above).
const REQUIRED = [...new Set([...PAIRS, ...WELL_BORDER_PAIRS].flatMap(([, fg, bg]) => [fg, bg]))]
const cssName = (name) => (name === 'well-border' ? `--${name}` : `--color-${name}`)

let failed = 0
for (const scope of ['dark', 'light']) {
  const t = tokens(scope)
  if (!t.canvas) {
    console.log(`\n${scope}: no palette found - is the block still there?`)
    failed += 1
    continue
  }

  // A missing token used to reach `ratio()` and throw an uncaught TypeError -
  // still a non-zero exit, so nothing silently passed, but a stack trace is
  // not a report. Name what is missing and move on to the next scope instead.
  const missing = REQUIRED.filter((name) => !t[name])
  if (missing.length > 0) {
    console.log(`\n${scope}: missing ${missing.map(cssName).join(', ')}`)
    failed += 1
    continue
  }

  console.log(`\n--- ${scope} ---`)
  for (const [label, fg, bg, floor] of [...PAIRS, ...WELL_BORDER_PAIRS]) {
    const value = ratio(t[fg], t[bg])
    const ok = value >= floor
    if (!ok) failed += 1
    console.log(`  ${label.padEnd(30)} ${String(value).padStart(6)}  ${ok ? 'ok' : `FAIL (needs ${floor})`}`)
  }
}

console.log(failed === 0 ? '\nevery pair clears its floor' : `\n${failed} failing`)
process.exit(failed === 0 ? 0 : 1)
