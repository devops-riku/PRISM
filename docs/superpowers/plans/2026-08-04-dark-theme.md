# Dark Studio Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin the PRISM studio app to a dark violet theme, while the two documents and every client-facing face stay on the light paper palette.

**Architecture:** Dark values are declared in the `@theme` block, so `<html>` carries them and everything - including Headless UI portals rendered at `<body>` - inherits dark for free. A single `.sheet-light` component class restores the light values for its subtree and is applied to four places. No component's colour utilities change: Tailwind v4 compiles `bg-paper` to `var(--color-paper)`, so redefining the variable on an ancestor re-skins everything beneath it.

**Tech Stack:** Tailwind v4 (CSS-first, no config files), React 18 + TypeScript strict, Vite. No frontend test runner - `npm run typecheck` and `npm run build` are the gate, plus one new dependency-free Node check for contrast.

## Global Constraints

- **Tailwind v4 is CSS-first.** There is no `tailwind.config.js` and no `postcss.config.js`. `frontend/src/index.css` is the single source of truth for every colour, size, radius, shadow and curve. Do not create config files.
- **Cascade layers resolve before specificity.** `@layer base` < `@layer components` < `@layer utilities`. A utility beats a component class regardless of selector weight. `.sheet-light` lives in `@layer components`.
- **TypeScript is strict** with `noUnusedLocals` and `noUnusedParameters`. An unused import fails the build.
- **No component colour utilities change.** If a task finds itself editing `text-ink` or `bg-paper` inside a `.tsx` file, it has gone wrong. The only `.tsx` edits in this plan add the string `sheet-light` to an existing `className`.
- **Backend is untouched, with one scoped exception.** No file under `backend/`
  changes except `app/design.py`'s two-entry `PALETTE` dict in Task 8, which is
  the printed document's own colours rather than the app's. No renderer, no PDF
  code, no schema.
- **Every colour value in this plan is exact.** Copy them character for character; they were chosen by measurement and several plausible-looking neighbours fail.
- **Contrast floor:** every text pair clears WCAG AA for body text, 4.5:1. Controls a user must locate - input borders - clear 3:1.

---

## File Structure

**Modified**

| File | Responsibility after this plan |
|---|---|
| `frontend/src/index.css` | The dark palette, `.sheet-light`, shadows, `color-scheme`, `.well`'s own border. Everything visual. |
| `frontend/index.html` | The boot preloader's own copy of the palette, with a comment saying it is a copy. |
| `frontend/src/components/MarkdownView.tsx` | Adds `sheet-light` to its root. One edit covers both documents, because both render through it. |
| `frontend/src/components/client/ClientShell.tsx` | Adds `sheet-light` to both of its roots. |
| `frontend/src/components/DesignEditor.tsx` | Adds `sheet-light` to the preview sheet. |

**Created**

| File | Responsibility |
|---|---|
| `frontend/scripts/check-contrast.mjs` | Reads the shipped `index.css`, computes every pair, exits non-zero if any falls below its floor. Dependency-free Node, so it runs anywhere the repo does. |

---

## Task 1: The dark palette

**Files:**
- Create: `frontend/scripts/check-contrast.mjs`
- Modify: `frontend/src/index.css` (the `@theme` colour block, the shadow block, the `html` rule in `@layer base`, `.well` in `@layer components`)

**Interfaces:**
- Consumes: nothing.
- Produces: the fourteen `--color-*` values every later task depends on, and `node scripts/check-contrast.mjs` as the repeatable gate.

After this task **everything is dark, documents included.** That is expected and temporary - Task 2 carves the light islands back out. Do not add `.sheet-light` here.

- [ ] **Step 1: Write the failing check**

Create `frontend/scripts/check-contrast.mjs`:

```js
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
  const body = scope === 'dark'
    ? css.slice(css.indexOf('@theme'), css.indexOf('}', css.indexOf('--color-alert-soft')))
    : css.slice(css.indexOf('.sheet-light'), css.indexOf('}', css.indexOf('.sheet-light')))
  const found = {}
  for (const [, name, hex] of body.matchAll(/--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    found[name] = hex
  }
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

let failed = 0
for (const scope of ['dark', 'light']) {
  const t = tokens(scope)
  if (!t.canvas) {
    console.log(`\n${scope}: no palette found - is the block still there?`)
    failed += 1
    continue
  }
  console.log(`\n--- ${scope} ---`)
  for (const [label, fg, bg, floor] of PAIRS) {
    const value = ratio(t[fg], t[bg])
    const ok = value >= floor
    if (!ok) failed += 1
    console.log(`  ${label.padEnd(30)} ${String(value).padStart(6)}  ${ok ? 'ok' : `FAIL (needs ${floor})`}`)
  }
}

// An input's edge has to be findable. It is the one non-text pair that matters:
// a divider nobody sees is a style, a field nobody can find is a fault.
const dark = tokens('dark')
const wellBorder = (css.match(/--well-border:\s*(#[0-9a-fA-F]{6})/) || [])[1]
if (!wellBorder) {
  console.log('\nno --well-border found')
  failed += 1
} else {
  for (const [label, bg] of [['on a card', 'paper'], ['on the page', 'canvas']]) {
    const value = ratio(wellBorder, dark[bg])
    const ok = value >= 3.0
    if (!ok) failed += 1
    console.log(`\n  input border ${label.padEnd(17)} ${String(value).padStart(6)}  ${ok ? 'ok' : 'FAIL (needs 3)'}`)
  }
}

console.log(failed === 0 ? '\nevery pair clears its floor' : `\n${failed} failing`)
process.exit(failed === 0 ? 0 : 1)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd frontend && node scripts/check-contrast.mjs`
Expected: FAIL. There is no `.sheet-light` block and no `--well-border` yet, so the light scope reports "no palette found" and the input border reports "no --well-border found". The dark scope will report the *light* values currently in `@theme`, which pass - that is fine, it is the two missing pieces that must fail here.

- [ ] **Step 3: Replace the palette**

In `frontend/src/index.css`, replace the fourteen `--color-*` lines inside `@theme` (currently the indigo-and-sand values, starting `--color-canvas: #f4f1ea;`) with:

```css
  /* ── Colour — the dark studio ─────────────────────────────────────────
     Near-black indigo, cards one step above it, one violet doing every
     action. The names describe the JOB, which is the whole reason a re-skin
     is a palette change rather than 54 files: `bg-paper` compiles to
     `var(--color-paper)`, so a component never learns what colour it is.

     DARK IS THE DEFAULT, AND IT IS DECLARED HERE so that <html> carries it.
     Headless UI renders dropdowns and the command palette into a portal at
     the end of <body> - a sibling of every shell, not a child of any - so
     anything scoped to a shell would leave every dropdown light. Inheriting
     from the root is the only arrangement where they are dark for free.
     `.sheet-light` below is how the four printed surfaces opt out.

     Every text pair was measured, not judged: headings 15.62:1 on a card,
     body 10.56, secondary 7.17, captions 5.13, the violet 5.27. */
  --color-canvas: #0e0e16; /* the page itself                       */
  --color-paper: #181826; /* cards, sheets, inputs                 */
  --color-duplicate: #202030; /* secondary fill: wells, chips, stripes  */
  --color-rule: #2c2c42; /* borders                               */
  --color-hairline: #242436; /* dividers inside a card                */
  --color-ink: #f2f1f7; /* headings                              */
  --color-body: #c9c7d8; /* body copy                             */
  --color-void: #a6a3bc; /* secondary copy                        */
  --color-faint: #8b88a3; /* eyebrows, captions, table headers     */
  --color-ballpoint: #8b7cf6; /* the one action colour, violet         */
  --color-accent-deep: #a79bf9; /* hover — LIGHTER, see below            */
  --color-accent-soft: #221f3a; /* accent tint background                */
  --color-alert: #f2777a; /* the single warm red, real problems    */
  --color-alert-soft: #2a1a1f; /* its tint                              */

  /* `accent-deep` INVERTS on a dark ground. On the light theme it was darker
     than the accent; here it is lighter. Hover means "more", and on a dark
     page more light is more - a hover that darkened would read as the control
     going away. */

  /* The edge of a text input, and the reason it is not `--color-rule`.
     `rule` is asked to be two things: the hairline between table rows, and the
     border of a field. On paper one value did both. On a dark card `#2c2c42`
     measures 1.29:1 as an edge - correct for a divider, invisible as a
     border, and a field whose edge cannot be found is a fault rather than a
     preference. This is the darkest value clearing 3:1 on BOTH the card and
     the page (3.10 and 3.40), because an input border should be locatable,
     not loud. `#3a3a55` through `#5c5c85` were tried first and measured 1.6
     to 2.78 - every one looked plausible, none passed. */
  --well-border: #636390;
```

- [ ] **Step 4: Replace the shadows**

Replace the four `--shadow-*` lines (currently `rgb(52 49 72 / …)`) with:

```css
  /* ── Shadows — light, not dark ────────────────────────────────────────
     A dark shadow on a near-black page is nothing at all. Depth here comes
     from a card being a step lighter than the ground and from a hairline of
     light along its top edge, which is what the reference does. The action
     shadow becomes the violet glow. */
  --shadow-sheet: inset 0 1px 0 rgb(255 255 255 / 0.04), 0 1px 2px
    rgb(0 0 0 / 0.4);
  --shadow-raised: inset 0 1px 0 rgb(255 255 255 / 0.06), 0 12px 28px -18px
    rgb(0 0 0 / 0.7);
  --shadow-action: 0 8px 20px -12px rgb(139 124 246 / 0.7);
  --shadow-ring: 0 0 0 4px rgb(139 124 246 / 0.22);
```

- [ ] **Step 5: Switch the root's colour scheme**

In `@layer base`, in the `html` rule, change `color-scheme: light;` to the
following. Unlike Step 6 this string occurs once (line 158), so there is nothing
to disambiguate - Task 2 adds the second occurrence, inside `.sheet-light`.

```css
    /* Native controls, form widgets and the scrollbar follow. Without it a
       date picker and a select still open white on a dark page. */
    color-scheme: dark;
```

- [ ] **Step 6: Give the input its own border**

`border: 1px solid var(--color-rule);` appears **six times** in this file. Change
**only the one inside `.well`** - it is at line 622 today, roughly ten lines below
`.well {` at 612. A find-and-replace here would recolour five borders that are
correctly quiet, and nothing would fail to tell you: the check script does not
look at them and the build does not care. Confirm you are inside `.well` by the
`padding-inline: 0.9rem;` two lines above it.

Change that one line to:

```css
    /* Not `--color-rule` - see that token's note. A divider and a field edge
       are different jobs and only one value can be quiet. */
    border: 1px solid var(--well-border);
```

- [ ] **Step 7: Run the check - the light scope should still fail**

Run: `cd frontend && node scripts/check-contrast.mjs`
Expected: the dark scope passes every pair and both input-border pairs pass; the **light** scope still fails with "no palette found", because `.sheet-light` does not exist yet. That is Task 2.

- [ ] **Step 8: Typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: both exit 0. No `.tsx` changed, so this is confirming the CSS still compiles.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/index.css frontend/scripts/check-contrast.mjs
git commit -m "The studio's palette goes dark"
```

---

## Task 2: The light islands

**Files:**
- Modify: `frontend/src/index.css` (add `.sheet-light` to `@layer components`)
- Modify: `frontend/src/components/MarkdownView.tsx:436`
- Modify: `frontend/src/components/client/ClientShell.tsx:145` and `:161`
- Modify: `frontend/src/components/DesignEditor.tsx:245`

**Interfaces:**
- Consumes: the `--color-*` names from Task 1. `.sheet-light` redefines exactly those fourteen.
- Produces: the class name `sheet-light`, which is the only string any later task or future screen needs to know.

- [ ] **Step 1: Add `.sheet-light`**

In `frontend/src/index.css`, inside `@layer components`, immediately before `.well`:

```css
  /* ── A printed page, inside a dark app ────────────────────────────────
     Restores the paper palette for one subtree. Everything below it renders
     exactly as it did before the app went dark, because the utilities never
     changed - only the values the variables hold.

     Four places use it: the document body (`MarkdownView`, which both the
     quotation and the proposal render through), `ClientShell`'s two roots,
     and `DesignEditor`'s preview sheet. Three of those are the same argument -
     this is what the client receives, so it should look like what the client
     receives - and the fourth is a picture of a printed page, which a dark
     preview would not preview.

     IT PAINTS ITS OWN PAPER as well as setting the variables, so a sheet
     dropped into a dark card renders as paper on a mount rather than as dark
     text on a dark ground. A utility on the same element still wins, which is
     what lets `ClientShell` keep its own `bg-canvas`.

     LIGHT IS OPT-IN, and that is a rule someone has to know: a new
     document-ish screen defaults to dark and has to ask for this. That is the
     right way round - a new screen matching the app it lives in is a smaller
     mistake than a new studio screen arriving white - but it is a rule rather
     than something the code will tell you.

     TWO SELECTORS, ONE BLOCK, and that is deliberate. `html[data-theme=light]`
     is the whole app in light mode (Task 6); `.sheet-light` is one printed
     surface inside the dark app. They want the identical fifteen values, and
     writing them twice is how the two drift - this branch has already shipped
     that mistake twice with a raster allowlist. One block, two doors into it.

     In light mode `.sheet-light` becomes a no-op that sets what is already
     set, which is correct rather than wasteful: a document is paper in both
     modes, so the class asserts the same thing whether or not the app agrees.
     */
  html[data-theme='light'],
  .sheet-light {
    --color-canvas: #f4f1ea;
    --color-paper: #fffdf8;
    --color-duplicate: #ede5d5;
    --color-rule: #ded3be;
    --color-hairline: #eae2d2;
    --color-ink: #343148;
    --color-body: #413e55;
    --color-void: #5a5670;
    --color-faint: #6e6a82;
    --color-ballpoint: #343148;
    --color-accent-deep: #252338;
    --color-accent-soft: #d7c49e;
    --color-alert: #a8443a;
    --color-alert-soft: #fcf4f2;
    /* NOT `--color-rule`, for the same reason the dark palette's is not: one
       token cannot be both a hairline and a findable input edge. `#ded3be` was
       specified here first and measures 1.46 against paper - the plan wrote
       the warning and then made the mistake. Measured 3.70 / 3.33 against
       light paper and light canvas. The light theme's inputs had been bordered
       at 1.46 since long before this plan; the gate is what found it. */
    --well-border: #8c836e;

    color-scheme: light;
    background-color: var(--color-paper);
    color: var(--color-body);
  }
```

- [ ] **Step 2: Harden the gate before you lean on it**

Task 1's review left three findings in `frontend/scripts/check-contrast.mjs`,
and two of them stop being theoretical the moment the block you just wrote
exists. Close them now, before Step 3 makes the light scope real.

**`--well-border` is resolved once, file-globally, and always measured against
the dark palette.** The block you just added defines its own
`--well-border: #ded3be`, so the light input-border pair - the 3:1 floor this
plan calls load-bearing - would never be checked at all. Move the lookup inside
`tokens()` so each scope resolves its own, and measure each against its own
`paper` and `canvas`.

**A missing token crashes instead of reporting.** The only completeness guard is
`!t.canvas`. If a future edit nests a rule inside the light block, the naive
first-`}` end-anchor truncates it mid-palette and `ratio()` throws an uncaught
TypeError. The exit code is still non-zero, so the gate does not silently pass -
but a stack trace is not a finding. Before computing any ratio, check that every
token the pairs name, and `--well-border`, resolved; if any did not, print
which ones are missing and exit non-zero.

**A stray `*/` at line 32** terminates a `//` comment block, left over from an
earlier draft. Delete it.

Then prove the hardening works rather than assuming it: temporarily give the
light block a failing `--well-border` (say `#e8e0d0`, far too pale against
`#fffdf8`), confirm the script now reports a light input-border FAIL, and put it
back.

- [ ] **Step 3: Run the check - both scopes should now pass**

Run: `cd frontend && node scripts/check-contrast.mjs`
Expected: PASS. Both `--- dark ---` and `--- light ---` print every pair with `ok`, and the script exits 0.

- [ ] **Step 4: The document body**

In `frontend/src/components/MarkdownView.tsx`, line 436, change:

```tsx
  return <div className="prose">{blocks}</div>
```

to:

```tsx
  // `sheet-light` because this IS the document - the bytes a client receives.
  // One edit covers both, since the quotation (`ResultSheets`) and the
  // proposal (`ProposalView`) both render their body through here. The dark
  // chrome around it stays dark, which is the frame a PDF viewer puts on a
  // page and the reason this is a sheet rather than a screen.
  return <div className="prose sheet-light">{blocks}</div>
```

- [ ] **Step 5: The client's own faces**

In `frontend/src/components/client/ClientShell.tsx`, add `sheet-light` to both roots.

Line 145, change:

```tsx
      <div className="min-h-dvh bg-canvas px-4 py-10 font-body text-body sm:px-6 sm:py-14">
```

to:

```tsx
      <div className="sheet-light min-h-dvh bg-canvas px-4 py-10 font-body text-body sm:px-6 sm:py-14">
```

Line 161, change:

```tsx
      className={`flex h-dvh items-center justify-center bg-canvas px-4 font-body text-body ${
```

to:

```tsx
      className={`sheet-light flex h-dvh items-center justify-center bg-canvas px-4 font-body text-body ${
```

- [ ] **Step 6: The design preview**

In `frontend/src/components/DesignEditor.tsx`, line 245, change:

```tsx
      className="mx-auto w-full max-w-[300px] overflow-hidden rounded-md border border-rule bg-white shadow-raised"
```

to:

```tsx
      className="sheet-light mx-auto w-full max-w-[300px] overflow-hidden rounded-md border border-rule bg-white shadow-raised"
```

`bg-white` stays. It was already right: this is a picture of a printed page, and `sheet-light` is what makes the border and shadow around it agree with the paper inside.

- [ ] **Step 7: The two colours hiding inside data-URIs**

Two SVGs are embedded in `index.css` as data-URIs, and their colours are
URL-encoded (`%23` is `#`). That is why the project's own "no hardcoded colour"
sweep missed them: a `#[0-9a-fA-F]{6}` search does not match `%231D1B17`. Both
carry values from **two palettes ago**.

`frontend/src/index.css:680` - the select's dropdown arrow, stroked
`%231D1B17`, the warm-brown ink from before the indigo re-skin. On a
`#181826` card it is invisible: the control still opens, and nothing tells you
it is a dropdown.

Replace that one occurrence of `stroke='%231D1B17'` with `stroke='%23A6A3BC'`
- `--color-void` in encoded form, the same weight this arrow had against paper.

`frontend/src/index.css:1076` - a checkmark stroked `%23FFFDFA`. **Leave the
value.** Add a comment saying why it does not track the accent.

An earlier draft of this plan told you to replace it with the dark canvas, on
the reasoning that it sits on the same violet as the primary button. That was
wrong, and Task 2's implementer caught it: `.prose` has exactly one call site,
and that element carries `sheet-light`, so this tick's ground is always the
LIGHT `--color-ballpoint` (`#343148`), never the dark theme's violet.
Near-white measures 12.32 against it; the dark canvas would have measured 1.54.
The instruction would have shipped an invisible checkmark.

Add above each one:

```css
    /* The colour here is URL-encoded (`%23` is `#`), which is why the
       project's hardcoded-colour sweeps never saw it. Kept in step with
       `--color-void` by hand; there is no way to put a custom property inside
       a data-URI. */
```

- [ ] **Step 8: Typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: both exit 0.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/index.css frontend/src/components/MarkdownView.tsx frontend/src/components/client/ClientShell.tsx frontend/src/components/DesignEditor.tsx
git commit -m "Documents and client faces stay on paper"
```

---

## Task 3: The boot preloader

**Files:**
- Modify: `frontend/index.html` (the `#boot` styles)

**Interfaces:**
- Consumes: the hex values from Task 1, copied by hand.
- Produces: nothing other tasks read.

The preloader carries its own colours and cannot do otherwise: it exists to paint before the stylesheet has loaded, so the tokens are not available to it. It is currently on the **pine** palette - `#35655a`, `#F6F3EE` - two generations stale, because nothing points at it and nobody remembered. Left alone it flashes cream, then pine, then the dark app.

- [ ] **Step 1: See the problem**

Run: `cd frontend && grep -n "#[0-9a-fA-F]\{6\}" index.html`
Expected: roughly fifteen matches including `#F6F3EE`, `#fbfaf8`, `#f2efeb`, `#5d5a55`, `#ded9d1` and `#35655a`. Note each one's line and what it is for before changing any.

- [ ] **Step 2: Recolour the preloader**

Map every colour found in Step 1 onto the dark palette, by role:

| was | is | role |
|---|---|---|
| `#F6F3EE` | `#0e0e16` | the page behind the loader |
| `#fbfaf8` | `#181826` | the card, if there is one |
| `#f2efeb` | `#202030` | the track a progress bar runs in |
| `#ded9d1` | `#2c2c42` | any rule or divider |
| `#5d5a55` | `#8b88a3` | the loading caption |
| `#35655a` | `#8b7cf6` | the accent - the bar, the mark |

Any colour not in this table takes the nearest role in it. Do not invent a value that is not in the palette.

Add this comment immediately above the `#boot` style block:

```html
    <!--
      These colours are a COPY of the palette in src/index.css, and they have
      to be: this screen paints before that stylesheet has loaded, which is the
      only reason it exists. They are kept in step by hand.

      Say so out loud, because the silence is what went wrong last time - this
      block sat on the pine palette through two re-skins, so the app opened
      cream, turned pine, then turned dark, and nobody noticed because nothing
      points here from anywhere. A copy that admits it is a copy can be found
      by anyone changing the palette. One that does not, cannot.
    -->
```

- [ ] **Step 3: Prove there is no light flash**

Run: `cd frontend && npm run build && npm run preview` and open the preview URL with the network throttled to "Slow 3G" in devtools, or run this against the dev server:

```bash
node -e "
const { chromium } = require('C:/Users/Riku/.claude/skills/gstack/node_modules/playwright-core');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
  await p.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded' });
  const shots = [];
  for (let i = 0; i < 6; i++) {
    shots.push(await p.evaluate(() => getComputedStyle(document.body).backgroundColor));
    await p.waitForTimeout(120);
  }
  console.log(shots.join('  '));
  await b.close();
})();
"
```

Expected: every sample is a dark colour - `rgb(14, 14, 22)` or the boot screen's own dark - and none is a light one such as `rgb(246, 243, 238)`.

- [ ] **Step 4: The favicon is pine green**

`frontend/index.html:21` is the tab icon, a data-URI whose colours are
URL-encoded: `%23F6F3EE` for the rounded square and `%2335655A` for the dot.
That second value is **pine** - the accent from before the indigo re-skin,
which was itself before this one. The tab has been wrong for two palettes for
the same reason the loading screen was: nothing points at it.

Replace `%23F6F3EE` with `%23181826` (the card surface, so the icon reads as a
tile rather than a white square in a dark tab strip) and `%2335655A` with
`%238B7CF6` (the accent).

Add the same note the preloader gets, in one line:

```html
    <!-- URL-encoded (%23 is #), which is why colour sweeps miss it. The card
         surface and the accent from src/index.css, kept in step by hand. -->
```

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html
git commit -m "The loading screen stops being two palettes out of date"
```

---

## Task 4: The sweep

**Files:**
- Modify: none expected. Any fix this task finds is a one-line correction in the file that owns it.

**Interfaces:**
- Consumes: everything above.
- Produces: the evidence that the theme is actually applied everywhere, which is the deliverable of this plan.

- [ ] **Step 1: Walk every studio screen**

With the dev servers running, visit each route and confirm no light surface appears inside dark chrome:

`#/` · `#/pad` · `#/quotations` · `#/proposals` · `#/documents` · `#/jobs` · `#/intakes` · `#/intakes/new` · `#/teams` · `#/workspaces` · `#/profile` · `#/settings`

On each: open any dropdown, and press `Ctrl+K` to open the command palette. **The palette and the dropdowns are the specific risk** - they render into a portal at `<body>`, so they are the one thing that would prove the root-level declaration wrong.

Record what was checked and what was seen. A screen that cannot be reached (an empty queue, no workspaces) is recorded as not checked rather than as passing.

- [ ] **Step 2: Confirm the documents are still paper**

Open a quotation and a proposal. The document body must be a light sheet framed by dark chrome. Open Settings and confirm the design preview is still a white page.

- [ ] **Step 3: Confirm the client's faces are untouched**

Open a client link. The form, the waiting face and the quotation face must all look exactly as they did before this plan - warm paper, indigo text.

If no live link is to hand, generate one from `#/intakes/new`.

- [ ] **Step 4: Run every gate**

```bash
cd frontend && node scripts/check-contrast.mjs && npm run typecheck && npm run build
```

Expected: all three exit 0.

Then the backend suite, to prove this plan touched nothing it should not have:

```bash
cd backend
for f in scripts/check_*.py scripts/smoke.py; do ./.venv/Scripts/python.exe "$f" >/dev/null 2>&1; echo "$(basename $f) $?"; done
```

Expected: every one `0`.

- [ ] **Step 5: Commit whatever the sweep fixed**

```bash
git add -A
git commit -m "Sweep: what the dark theme missed"
```

If the sweep found nothing, say so and skip the commit rather than making an empty one.

---

## Task 5: The ambient layer and the navbar

**Files:**
- Modify: `frontend/src/index.css` (a `.page-glow` component class)
- Modify: `frontend/src/App.tsx` (the five shells, and the `nav` element at 824)
- Modify: `frontend/src/components/HomeScreen.tsx` (the running-jobs stat card)

**Interfaces:**
- Consumes: the dark tokens from Task 1.
- Produces: `.page-glow`, applied by the shells.

The reference is not a flat dark page. It has a soft violet wash behind the
hero, brightest at the top centre and gone by a third of the way down, and the
navbar reads as its own surface with a hairline under it. Without those the
palette is right and the screen still is not.

- [ ] **Step 1: The ambient glow**

In `@layer components` in `index.css`:

```css
  /* The wash behind the hero. One radial, anchored to the top centre and gone
     by two thirds of the way down, in the accent at a tenth of its strength.

     On the SHELL rather than on `body`, because the shells are what carry
     `overflow: hidden` and a fixed height - a gradient on the body would be
     painted behind a page that never scrolls.

     `background-attachment` is deliberately not set: the shells do not scroll,
     so there is nothing to fix against, and it costs a compositing layer on
     every one of them. */
  .page-glow {
    background-image: radial-gradient(
      ellipse 90% 55% at 50% 0%,
      color-mix(in oklab, var(--color-ballpoint) 12%, transparent),
      transparent 70%
    );
    background-repeat: no-repeat;
  }
```

- [ ] **Step 2: Put it on the shells**

In `frontend/src/App.tsx`, add `page-glow` to the `className` of every shell
`<div>` that already carries `bg-canvas`. There are five. Add the string only;
change nothing else about them.

Find them with: `grep -n "bg-canvas font-body text-body" src/App.tsx`

- [ ] **Step 3: The navbar reads as its own surface**

In `frontend/src/App.tsx`, the `nav` element defined at line 824. Add
`border-b border-hairline bg-paper/60` to the outer element's existing classes,
and nothing else.

`bg-paper/60` rather than `bg-paper`: a solid bar would cut the wash in half
across the top of the screen, and the glow showing through the navbar is what
makes it read as a surface on the page rather than a lid on top of it.

- [ ] **Step 4: The stat card takes the accent tint**

In `frontend/src/components/HomeScreen.tsx`, the card showing the running-job
count - find it by `{figure(running)}` around line 325. In the reference it is
the one panel carrying the accent rather than the plain card surface.

Change only that container's background and border classes to
`bg-accent-soft` and `border-ballpoint/30`. Leave its layout, padding and type
alone.

- [ ] **Step 5: The comment that did not land**

Task 2's review left one minor, and it is a documentation gap in the file you
are already editing. The light `--well-border` was corrected from `#ded3be` to
`#8c836e` mid-task, and the explanation for it went into the plan document but
never into `index.css`. So a reader of that file alone finds `#8c836e` sitting
beside a `--color-rule` of `#ded3be` with nothing saying why they differ - next
to a dark `--well-border` that is thoroughly commented for exactly this reason.

Add above the light `--well-border` declaration (around line 673):

```css
    /* NOT `--color-rule`, for the same reason the dark one is not: a hairline
       and a findable input edge are different jobs and one value cannot be
       quiet enough for both. `#ded3be` - the same hex as `--color-rule` - was
       specified here first and measures 1.46 against paper. Measured 3.70 and
       3.33 against light paper and light canvas.

       Worth separating: the light theme's inputs were bordered at 1.46 long
       before the dark theme existed. This is not a regression that was fixed,
       it is a gap the contrast gate found the first time anything measured
       it. */
```

- [ ] **Step 6: Verify**

```bash
cd frontend && node scripts/check-contrast.mjs && npm run typecheck && npm run build
```

Expected: all three exit 0. The glow is a background image and changes no token,
so the contrast check is confirming nothing regressed rather than measuring the
glow itself.

Then screenshot the home screen and confirm three things: a visible wash at the
top centre that has faded out before the cards, a navbar distinguishable from
the page, and the stat card tinted against the five plain ones.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/index.css frontend/src/App.tsx frontend/src/components/HomeScreen.tsx
git commit -m "The wash behind the hero, and a navbar that is its own surface"
```

---

## Task 6: Light mode

**Files:**
- Create: `frontend/src/lib/theme.ts`
- Modify: `frontend/index.html` (apply the stored choice before first paint)
- Modify: `frontend/src/App.tsx` (mount the theme, add the control to `nav`)

**Interfaces:**
- Consumes: the shared light block from Task 2, which `html[data-theme='light']`
  already selects. **This task adds no colour values.** If you find yourself
  typing a hex, stop - the values exist, and duplicating them is exactly what
  Task 2's own comment warns against.
- Produces: `readTheme()`, `applyTheme(theme)`, `toggleTheme()` from
  `frontend/src/lib/theme.ts`.

Dark stays the default. Light is a choice the studio makes and PRISM remembers.

- [ ] **Step 1: The module**

Create `frontend/src/lib/theme.ts`:

```ts
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
 *  absence of a choice: the CSS carries one selector instead of two. An
 *  install that has never touched this RENDERS the same as one that switched
 *  to light and back - which is what makes removing it safe. They are not
 *  indistinguishable, only equivalent on screen: raw storage can still tell
 *  them apart. */
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
  // The DOM, not storage. `applyTheme` sets the attribute OUTSIDE its
  // try/catch, so the attribute is correct even when the write that follows
  // it fails - and on a browser where writes never persist (Safari's private
  // mode, a locked profile: the cases `readTheme` goes to trouble to survive)
  // asking storage what the current theme is returns the pre-failure answer
  // for ever, and this button stops doing anything after one press. The label
  // freezes and the click does nothing: a dead control, which is worse than a
  // wrong colour.
  //
  // An earlier draft of this module read `readTheme()` here, which defeated
  // its own storage-failure handling by trusting storage for the one question
  // it already knew the answer to.
  const current: Theme =
    document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
  const next: Theme = current === 'light' ? 'dark' : 'light'
  applyTheme(next)
  return next
}
```

- [ ] **Step 2: Apply it before first paint**

In `frontend/index.html`, inside the existing boot script, as the FIRST thing it
does:

```js
      // Before anything paints. Reading this in React instead would mean a
      // dark frame on every load for a studio who chose light - the same flash
      // this whole block exists to prevent.
      try {
        if (window.localStorage.getItem('prism.theme') === 'light') {
          document.documentElement.setAttribute('data-theme', 'light')
        }
      } catch (e) {}
```

The boot preloader's own colours (Task 3) stay dark regardless. That is a known
and accepted seam: a studio in light mode sees a dark splash for the width of
the load. Making the splash follow the theme means reading storage twice and
branching its inline styles, for a surface that is visible for well under a
second. If it ever matters, that is its own change.

- [ ] **Step 3: The control**

In `frontend/src/App.tsx`, import from `./lib/theme`:

```tsx
import { readTheme, toggleTheme } from './lib/theme'
import type { Theme } from './lib/theme'
```

Add the state beside the component's other hooks:

```tsx
  const [theme, setTheme] = useState<Theme>(() => readTheme())
```

And in the `nav` element, beside the notification bell:

```tsx
            <button
              type="button"
              onClick={() => setTheme(toggleTheme())}
              aria-label={theme === 'light' ? 'Switch to dark' : 'Switch to light'}
              title={theme === 'light' ? 'Switch to dark' : 'Switch to light'}
              className="rounded-lg p-2 text-void transition-colors hover:text-ink"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-[18px] w-[18px]"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                {theme === 'light' ? (
                  <path d="M20 13a8 8 0 1 1-9-9 6.5 6.5 0 0 0 9 9Z" />
                ) : (
                  <>
                    <circle cx="12" cy="12" r="4" />
                    <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
                  </>
                )}
              </svg>
            </button>
```

The icon shows what pressing it gives you, not what you are in: a moon in dark
mode is a control that describes its own state and does nothing you can act on.

- [ ] **Step 4: Verify both modes**

```bash
cd frontend && node scripts/check-contrast.mjs && npm run typecheck && npm run build
```

The contrast check already measures both palettes - it reads the dark block and
the shared light block - so light mode is covered without touching the script.

Then in the browser:
1. Toggle to light. The whole app is the paper palette; open a quotation and
   confirm the document still looks right.
2. **Reload.** It must still be light, with no dark frame first.
3. Toggle back to dark and reload again.

Step 2 is the one that matters. A toggle that works until you refresh is the
common failure here, and it is invisible until somebody refreshes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/theme.ts frontend/index.html frontend/src/App.tsx
git commit -m "Light mode, remembered, and applied before the first paint"
```

---

## Task 7: The document on screen follows the app

**Files:**
- Modify: `frontend/src/components/MarkdownView.tsx` (remove `sheet-light` from the root)

**Interfaces:**
- Consumes: `.sheet-light` from Task 2, which stays and is still used by the other two surfaces.
- Produces: nothing.

**This reverses a decision the spec argues for, deliberately and at the studio's
request.** The spec had the on-screen quotation and proposal stay a white sheet
inside dark chrome, on the reasoning that what you proofread should look like
what your client receives. The studio has since asked for the document to
follow the app instead, having seen it. That is their call to make about their
own tool, and the reasoning it overturns is worth keeping visible rather than
deleting, so the comment below says what was traded away.

**`.sheet-light` does not go away.** Two surfaces still need it and for reasons
this change does not touch:

- `ClientShell` - the client's faces stay paper. A stranger opening a link once
  is not the studio living in the tool all day, and that was a separate decision
  which still stands.
- `DesignEditor`'s preview sheet - it is a picture of a printed page. A dark
  preview previews nothing.

- [ ] **Step 1: Remove it from the document body**

In `frontend/src/components/MarkdownView.tsx`, the root return. Change:

```tsx
  return <div className="prose sheet-light">{blocks}</div>
```

to:

```tsx
  // NO `sheet-light` here, and the history matters because it was here on
  // purpose. The plan's original ruling was that a document on screen should
  // be the paper the client receives - a white sheet framed by dark chrome,
  // like a PDF viewer - so that proofreading and sending showed the same
  // thing. The studio saw it and asked for the document to follow the app
  // instead.
  //
  // What that trades away, stated so nobody rediscovers it as a bug: the
  // on-screen document and the PDF it becomes now look deliberately
  // different. The screen follows the app's theme; the paper follows
  // `ProposalDesign`. They are two answers to two different questions rather
  // than a drift.
  //
  // `.sheet-light` itself stays - `ClientShell` and `DesignEditor`'s preview
  // still carry it, for reasons this change does not touch.
  return <div className="prose">{blocks}</div>
```

- [ ] **Step 2: Check what `.prose` hard-sets**

`.prose` sets `color: var(--color-ink)` on itself (`index.css`, around line 1027). Inside a dark app that now resolves to the dark ink, which is what this task wants - but confirm by reading that nothing else in `.prose` or its nested rules pins a light-only value: a hardcoded background, a `border-color` that assumed paper, a `color` on a table header.

Report every such value you find. Fix only the ones that are now illegible, and say which you fixed and which you left.

- [ ] **Step 3: Verify**

```bash
cd frontend && node scripts/check-contrast.mjs && npm run typecheck && npm run build
```

All three exit 0. The gate still measures both scopes - the light scope is still
real because `.sheet-light` still exists.

Then look at a document. If you cannot reach one (it needs a signed-in session),
say so plainly rather than reporting the check as done, and render `.prose`
markup against the built CSS in a standalone page instead - stating that is what
you did.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MarkdownView.tsx frontend/src/index.css
git commit -m "The document on screen follows the app it is read in"
```

---

## Task 8: The printed document takes the palette

**Files:**
- Modify: `backend/app/design.py` (the `PALETTE` dict)
- Modify: `frontend/src/components/DesignEditor.tsx` (its mirror of the same two values)

**Interfaces:**
- Consumes: nothing from earlier tasks. This is the paper document, not the app.
- Produces: new defaults for `ProposalDesign.brand_colour` and `.accent_colour`.

**This task is the one exception to the plan's "no `backend/` changes"
constraint**, and it is scoped to two string literals in one dict. Nothing else
under `backend/` may change.

The document defaults are **two palettes stale**, the fourth instance of that
pattern on this branch after the boot preloader, the favicon, and the two
data-URI colours:

```python
PALETTE = {"brand": "#1D1B17", "accent": "#35655A"}
```

`#1D1B17` is the original warm-brown ink. `#35655A` is **pine** - the accent from
two re-skins ago. Every proposal PDF built with the defaults has been printing a
pine accent bar since long before any of this.

- [ ] **Step 1: The backend values**

In `backend/app/design.py`, replace the `PALETTE` dict:

```python
#: The document's own two colours, and they are NOT the app's.
#:
#: A quotation is printed and emailed; the app is looked at on a screen. So the
#: brand is the indigo the app uses for its own ink, which is legible on paper
#: at 12.51:1 - and the accent is a DEEPER violet than the app's `#8b7cf6`,
#: because it does a different job here. In the app that violet is type on a
#: dark ground. Here it is a filled banner with paper-coloured text ON it, and
#: white on `#8b7cf6` measures 3.33 - under AA for body text. `#6d57e8` is the
#: same violet family at 5.03, which clears it with headroom.
#:
#: Mirrored by hand in `frontend/src/components/DesignEditor.tsx`, which cannot
#: import from Python. Change one and change the other.
PALETTE = {"brand": "#343148", "accent": "#6D57E8"}
```

- [ ] **Step 2: The frontend's mirror**

In `frontend/src/components/DesignEditor.tsx`, around line 76:

```tsx
/** `app/design.py`'s own `PALETTE`, mirrored - this file cannot import from
 *  Python, so the two are held in step by hand and each names the other.
 *  These are the DOCUMENT's colours, not the app's: the accent is a deeper
 *  violet than `--color-ballpoint` because here it is a filled banner with
 *  paper text on it rather than type on a dark ground. */
const PALETTE = { brand: '#343148', accent: '#6D57E8' }
```

- [ ] **Step 3: Prove the new accent is legible where it is actually used**

The accent prints as the banner cover's fill, with paper-coloured text on top,
and as the page's edge bar. Render a proposal PDF with the new defaults and
confirm the cover is readable.

There are five proposal documents on disk under `backend/generated/w/riku/_documents/`.
Render one both ways and compare, from `backend/`:

```python
import sys
sys.path.insert(0, r"C:\Users\Riku\OneDrive\OneDrive - Countpro PH\Desktop\PRISM-\backend")
from app import workspaces, documents as docs_module, main as m
from app.design import ProposalDesign
from app.renderers.pdf import render_pdf
workspaces.ensure_ready(); workspaces.use("riku")
import pathlib
newest = sorted(pathlib.Path("generated/w/riku/_documents").glob("*.json"),
                key=lambda p: p.stat().st_mtime, reverse=True)[0]
doc = docs_module.get(newest.stem)
md, est = m._document_markdown(doc), m._document_estimate(doc)
for label, look in (("stored", doc.design), ("new defaults", ProposalDesign())):
    data = render_pdf(md, "T", est, kind="proposal", doc_label="Proposal",
                      cover_break=True, design=look)
    print(f"{label:14} {len(data):>9,} bytes  accent={look.accent_colour}")
```

Both must render without raising. Report the byte counts and the accent each
used.

- [ ] **Step 4: Say plainly what this does and does not change**

`ProposalDesign` is **snapshotted onto each document when it is built**. So these
new defaults reach:

- every proposal and quotation built from now on, **if** the studio has not set
  its own colours in Settings;
- **not** the five documents already on disk, which keep the design they were
  built with.

The studio's live settings already carry their own `brand_colour` and
`accent_colour`, so on this install the defaults may be overridden anyway. Check
`backend/generated/w/riku/` settings and report which is the case - a default
nobody uses is worth knowing about before it is called a fix.

- [ ] **Step 5: Verify**

From `backend/`, every check must still pass:

```bash
for f in scripts/check_*.py scripts/smoke.py; do ./.venv/Scripts/python.exe "$f" >/dev/null 2>&1; echo "$(basename $f) $?"; done
```

Expected: every one `0`, except `check_kind_render.py`, which must also be `0` -
it pins a byte-for-byte hash of a rendered document and **a colour change may
alter that hash**. If it fails, that is this task's regression to deal with, not
a pre-existing one: read its failure, and if the hash moved because the accent
moved, the fixture's baseline needs recomputing with an explanation - do not
simply recompute it silently.

Then from `frontend/`: `npm run typecheck && npm run build`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/design.py frontend/src/components/DesignEditor.tsx
git commit -m "The printed document stops defaulting to pine"
```

---

## Task 9: The app gets its own width

**Files:**
- Modify: `frontend/src/index.css` (a new `--container-app` token)
- Modify: `frontend/src/App.tsx` (the shells that are app chrome)
- Modify: `frontend/src/components/AppHeader.tsx` (the navbar's own inner width)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `--container-app`, which Tailwind v4 exposes as `max-w-app`.

Every shell in this app is capped at `--container-sheet`, 1080px. On a 1920
screen that leaves roughly 420px of empty page either side, and the studio's own
description is exact: the content reads as sitting in the centre rather than
filling the screen. A five-card row at 1080 gives each card about 230px; the
same row at 1400 gives about 264px and stops looking like a column.

**`--container-sheet` must not change.** It is the width of a printed page, used
by `.sheet` (`index.css`, around line 984) and by the document routes. 1080px is
a reading measure, chosen for prose, and widening it would make a quotation
harder to read to fix a home screen.

So this is a second token, and the two names then say which is which: a sheet is
paper, an app is not.

- [ ] **Step 1: The token**

In `index.css`'s `@theme`, beside the existing container tokens:

```css
  /* The app's own chrome - lists, the queue, the home screen, the pad. Wider
     than a sheet on purpose: `--container-sheet` is a READING measure for
     prose on a printed page, and a five-across grid of cards is not prose.
     Capping the app at the paper width left about 420px of empty page either
     side on a 1920 screen and made the whole product read as a column.

     Documents keep `--container-sheet`. That is the distinction the two names
     carry, and it is the reason this is a new token rather than a bigger
     number in the old one. */
  --container-app: 1400px;
```

- [ ] **Step 2: Point the app's shells at it**

In `frontend/src/App.tsx`, find every `max-w-sheet`:

```bash
grep -n "max-w-sheet" src/App.tsx
```

There are five. Change to `max-w-app` **only the shells that are app chrome** -
the grouped route shell, the home shell, and the pad. **Leave the quotation
route's shell on `max-w-sheet`**: that route is a document page and its width is
the page's width.

If you cannot tell which is which from the code, read the comment above each
`return` - they say what each shell is for. Report which you changed and which
you left, by line.

- [ ] **Step 3: The navbar follows the shell**

`frontend/src/components/AppHeader.tsx` carries its own `max-w-sheet` so the
navbar's contents line up with the page's. It must match whatever the shell
under it uses, or the wordmark and the avatar will no longer sit above the
content they belong to.

The navbar renders on both the widened shells and the quotation route's
`max-w-sheet` one. Pick the honest fix rather than the quick one: either the
header takes a width prop from each shell, or it takes `max-w-app` and the
quotation route accepts a wider bar than its sheet. **Say which you chose and
why in your report** - a header misaligned on one route is exactly the kind of
thing that reads as sloppiness rather than as a decision.

- [ ] **Step 4: Documents keep a reading measure**

Widening a shell must not widen a document inside it. Check the proposal route,
which renders `ProposalView` inside a shell you may have just widened.

Confirm the rendered document body still has a reading width. If it does not -
if `.prose` now stretches to 1400px - give the document's own container
`max-w-sheet` so the sheet stays a sheet inside a wider app.

Report what you found, with the measured width of the document body before and
after.

- [ ] **Step 5: A comment that does not match its own code**

Task 5's review left one informational minor, in the file you are already
editing and about the shell you are about to change. `.page-glow`'s doc comment
(`index.css`, around line 379) says three things, and two of them are not true
of the code beneath it:

- "the accent at a tenth of its strength" - the value is `12%`.
- "gone by two thirds of the way down" - the stop is at `70%`.
- "the shells are what carry `overflow: hidden` and a fixed height" - the
  quotation route's shell (`App.tsx:935`) is `min-h-screen` and scrolls. That
  is the one shell whose width you are deliberately leaving alone in Step 2,
  so you will be looking straight at the counter-example.

The text came from the brief and was copied verbatim, correctly - it is mine,
not the implementer's. Correct it now.

Say the actual numbers, and rewrite the third claim to what is true: the glow
sits on the shells rather than on `body` because a gradient on `body` is
painted behind the whole document, and most of these shells are fixed to the
viewport - with the quotation route as the exception that scrolls.

A comment that disagrees with the code beside it is how the next person learns
to stop reading the comments.

- [ ] **Step 6: Verify**

```bash
cd frontend && node scripts/check-contrast.mjs && npm run typecheck && npm run build
```

All three exit 0. This task changes no colour, so the gate is confirming you
broke nothing.

Then measure, at 1920x1080 and at 1440x900:

- the shell's rendered width on the home screen (expect ~1400 at 1920, and
  viewport-minus-padding at 1440),
- the navbar's inner width, which must equal the shell's on the same route,
- the document body's width on the proposal route, which must still be a
  reading measure.

The dev server is on `http://localhost:5173` and the API on `:8000` - **do not
restart or kill them, the user is using them.** A playwright-core install is at
`C:/Users/Riku/.claude/skills/gstack/node_modules/playwright-core`. The studio
screens need a signed-in session; if you cannot reach them, say so plainly and
measure what you can from the built CSS instead, stating which is which.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/index.css frontend/src/App.tsx frontend/src/components/AppHeader.tsx
git commit -m "The app stops being capped at the width of a page"
```

---

## Self-Review

**Spec coverage.** Every section of `2026-08-04-dark-theme-design.md` maps to a task: the palette and the two measured findings are Task 1 (`--well-border` included, with the failed candidates recorded so nobody re-picks one); the boundary and all four light islands are Task 2; the preloader is Task 3; the verification list is Task 4, with the contrast half made permanent as `check-contrast.mjs` rather than done once by hand.

**Placeholders.** None. Every colour is an exact hex, every command is runnable, every code block is the literal text to write. The one judgement call left to the implementer is Step 2 of Task 3 - mapping any preloader colour not in the table - and it is bounded by "do not invent a value that is not in the palette".

**Type consistency.** No types are introduced. The one identifier crossing task boundaries is the class name `sheet-light`, spelled identically in Task 2's CSS and in all four `.tsx` edits. `--well-border` is defined in Task 1 and read in Task 1's `.well` and Task 2's `.sheet-light`.

**One deliberate gap.** Task 1 leaves the app in a state where documents are dark, which is wrong on its own and fixed by Task 2. It is split that way because the palette and the boundary can each be rejected independently, and a reviewer who sees them together cannot tell which one a fault belongs to.
