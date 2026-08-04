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
- **Backend is untouched.** No file under `backend/` changes. No renderer, no PDF, no `ProposalDesign`.
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
     than something the code will tell you. */
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
    --well-border: #ded3be;

    color-scheme: light;
    background-color: var(--color-paper);
    color: var(--color-body);
  }
```

- [ ] **Step 2: Run the check - both scopes should now pass**

Run: `cd frontend && node scripts/check-contrast.mjs`
Expected: PASS. Both `--- dark ---` and `--- light ---` print every pair with `ok`, and the script exits 0.

- [ ] **Step 3: The document body**

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

- [ ] **Step 4: The client's own faces**

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

- [ ] **Step 5: The design preview**

In `frontend/src/components/DesignEditor.tsx`, line 245, change:

```tsx
      className="mx-auto w-full max-w-[300px] overflow-hidden rounded-md border border-rule bg-white shadow-raised"
```

to:

```tsx
      className="sheet-light mx-auto w-full max-w-[300px] overflow-hidden rounded-md border border-rule bg-white shadow-raised"
```

`bg-white` stays. It was already right: this is a picture of a printed page, and `sheet-light` is what makes the border and shadow around it agree with the paper inside.

- [ ] **Step 6: Typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: both exit 0.

- [ ] **Step 7: Commit**

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

- [ ] **Step 4: Commit**

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

## Self-Review

**Spec coverage.** Every section of `2026-08-04-dark-theme-design.md` maps to a task: the palette and the two measured findings are Task 1 (`--well-border` included, with the failed candidates recorded so nobody re-picks one); the boundary and all four light islands are Task 2; the preloader is Task 3; the verification list is Task 4, with the contrast half made permanent as `check-contrast.mjs` rather than done once by hand.

**Placeholders.** None. Every colour is an exact hex, every command is runnable, every code block is the literal text to write. The one judgement call left to the implementer is Step 2 of Task 3 - mapping any preloader colour not in the table - and it is bounded by "do not invent a value that is not in the palette".

**Type consistency.** No types are introduced. The one identifier crossing task boundaries is the class name `sheet-light`, spelled identically in Task 2's CSS and in all four `.tsx` edits. `--well-border` is defined in Task 1 and read in Task 1's `.well` and Task 2's `.sheet-light`.

**One deliberate gap.** Task 1 leaves the app in a state where documents are dark, which is wrong on its own and fixed by Task 2. It is split that way because the palette and the boundary can each be rejected independently, and a reviewer who sees them together cannot tell which one a fault belongs to.
