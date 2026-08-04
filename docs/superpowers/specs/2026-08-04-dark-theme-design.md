# The studio goes dark

**Date:** 2026-08-04
**Scope:** `frontend/` only. No backend change, no change to PDFs or print output.

## What this is

The studio app is re-skinned to a dark theme: a near-black indigo page, cards
one step lighter, a violet accent, and a violet edge on hover. The reference is
the studio's own screenshot of the home screen and of a card's hover state.

Three surfaces do **not** go dark, and each for its own reason:

| Surface | Stays light | Because |
|---|---|---|
| The quotation view | yes | It is the document a client receives. What is on screen should be what they get. |
| The proposal view | yes | Same. |
| `DesignEditor`'s preview sheet | yes | It is a picture of the printed page. A dark preview would preview nothing. |
| `ClientShell` (every client face) | yes | Not the studio's screen. A stranger opens it once from a link, often on a phone, and a dark form arriving unannounced from an unfamiliar company reads differently to a light one. |

**Not in scope.** No light/dark toggle. No `prefers-color-scheme` support. No
component rewrites. No change to `ProposalDesign` - the colours a studio picks
for its documents are a separate setting and are untouched.

## Why this is small

The app is already tokenised. Across 54 components there is no `text-white`, no
`bg-black`, and colour reaches the screen through `@theme` custom properties in
every case but two:

- `AuthScreen.tsx` - the Google and Facebook logo paths. Brand marks. Untouched.
- `DesignEditor.tsx:245` - `bg-white` on the preview sheet. Correct already: that
  sheet is a printed page, and it is one of the light islands below.

Tailwind v4 compiles `bg-paper` to `var(--color-paper)`. Redefining that variable
on an ancestor re-skins every utility beneath it, so the work is a palette and a
boundary rather than 54 files.

## The boundary

**Dark is the default, declared on `<html>`. Light is opt-in.**

```
html                     -> the dark values (the @theme block)
  .sheet-light           -> restores today's light values for its subtree
```

`.sheet-light` goes on four places: the quotation view's document frame, the
proposal view's document frame, `DesignEditor`'s preview sheet, and
`ClientShell`'s root.

**Why dark at the root rather than on the studio shell.** Headless UI dropdowns
and the command palette render into a portal at the end of `<body>` - a sibling
of every shell, not a child of any. This was proven while chasing an unrelated
scrollbar bug: a portal is outside the shell's containment entirely. Anything
scoped to the studio shell would leave every dropdown light, and each new
portalled component would be a fresh bug found by eye rather than by rule.
Inheriting from `<html>` is the only arrangement where they are dark for free.

**The cost, stated because someone will meet it.** Light islands are opt-in, so
a new document-ish screen defaults to dark and has to ask for light. That is the
right failure direction - a new screen matching the app it lives in is a smaller
mistake than a new studio screen arriving white - but it is a rule that has to
be known rather than discovered.

## The palette

Fourteen tokens, the same names, dark values. The names describe the JOB, which
is what makes a re-skin a palette change.

```
--color-canvas:       #0E0E16   the page
--color-paper:        #181826   cards, sheets, inputs
--color-duplicate:    #202030   wells, chips, stripes
--color-rule:         #2C2C42   borders
--color-hairline:     #242436   dividers inside a card
--color-ink:          #F2F1F7   headings
--color-body:         #C9C7D8   body copy
--color-void:         #A6A3BC   secondary copy
--color-faint:        #8B88A3   eyebrows, captions, table headers
--color-ballpoint:    #8B7CF6   the one action colour, violet
--color-accent-deep:  #A79BF9   hover
--color-accent-soft:  #221F3A   the violet tint
--color-alert:        #F2777A   the single warm red
--color-alert-soft:   #2A1A1F   its tint
```

The light values these replace are preserved verbatim under `.sheet-light`:

```
canvas #f4f1ea  paper #fffdf8  duplicate #ede5d5  rule #ded3be  hairline #eae2d2
ink #343148  body #413e55  void #5a5670  faint #6e6a82
ballpoint #343148  accent-deep #252338  accent-soft #d7c49e
alert #a8443a  alert-soft #fcf4f2
```

### `accent-deep` inverts

On the light theme `accent-deep` was *darker* than the accent. On dark it is
*lighter*. Hover means "more", and on a dark ground more light is more. A hover
state that darkened would read as a control going away.

## Two findings from measuring

Every pair below was computed, not judged by eye.

### The primary button takes dark text, not white

On `#8B7CF6`:

| text | ratio |
|---|---|
| near-black (`canvas`) | **5.77** |
| white | 3.33 |
| `ink` | 2.96 |

White on this violet fails AA for body text. The filled button is therefore
dark-on-violet, which is also what the reference does with its pale button on a
dark card.

### `--color-rule` is doing two jobs and can only do one in the dark

`rule` is both the hairline between table rows and the edge of a text input. At
`#2C2C42` on a `#181826` card it measures **1.29:1** - right for a divider,
invisible as an input border, and a field whose edge cannot be found is a real
usability failure rather than a cosmetic one.

**Fix:** `rule` stays quiet for dividers, and `.well` - the input class, already
hand-written in `index.css` - gets its own brighter border there. No component
changes. This is the same property that makes the whole re-skin possible.

The value is `#636390`, measured at **3.10:1** against `--color-paper` and
**3.40:1** against `--color-canvas`, so a field is findable whether it sits on a
card or straight on the page. It is the darkest value that clears 3:1 on both,
which is the point: an input border should be locatable, not loud. A first pass
tried `#3A3A55` through `#5C5C85` and every one of them measured between 1.6 and
2.78 - all of them looked plausible and none of them passed.

### Card-against-page is deliberately below 3:1

`paper` on `canvas` measures 1.1:1. That is correct and matches the reference,
which gets its depth from a barely-lighter card rather than a visible edge. The
3:1 non-text rule applies to controls a user must locate, not to a panel they
are already reading.

## Contrast budget

Every pair must clear WCAG AA for body text (4.5:1). Measured against the
proposed values:

| pair | ratio |
|---|---|
| headings on a card | 15.62 |
| body on a card | 10.56 |
| secondary on a card | 7.17 |
| captions on a card | 5.13 |
| captions on the page | 5.62 |
| body on the page | 11.57 |
| violet link on a card | 5.27 |
| violet on the page | 5.77 |
| violet glyph on its tint | 4.76 |
| alert on a card | 6.43 |

## Depth without shadows

`--shadow-sheet` is `rgb(52 49 72 / 0.18)`. A dark shadow on a near-black page is
nothing at all.

- `--shadow-sheet` and `--shadow-raised` become light-on-dark: a low-opacity
  white lift plus a darker ambient, so a card reads as raised rather than drawn.
- `--shadow-action` and `--shadow-ring` become the violet glow - the accent at
  low opacity, which is what the reference's focus and hover states are.
- `color-scheme: dark` on `<html>`, so native controls, form widgets and the
  scrollbar follow. `.sheet-light` sets `color-scheme: light` for its subtree.
- The scrollbar tokens added recently (`scrollbar-color`, the `::-webkit-`
  block) resolve from `--color-rule`, so they follow the palette without edits.

## The boot preloader

`frontend/index.html` carries 43 hardcoded colours for the loading screen, and
they are still the **pine** palette - `#35655a`, `#F6F3EE` - two generations
stale. It never got the indigo update because it is a hand-maintained copy that
nothing points at.

Left alone it would flash cream, then pine, then the dark app.

It takes the dark values, and it **keeps its own copy of them**. That is decided
rather than left open: the preloader exists precisely to paint before the
stylesheet has loaded, so reading the tokens is not available to it. What it
gains is a comment naming `index.css` as the source and saying plainly that the
two are held in step by hand. A copy that admits it is a copy is survivable; one
that does not is what produced a loading screen two palettes out of date.

## Verification

1. The contrast table above, re-computed against the values actually shipped in
   `index.css` rather than the values in this document. Zero pairs below AA.
2. `.well`'s border measured at 3:1 or better against `--color-paper`.
3. A headless pass over every studio screen and both client faces, asserting:
   no light surface inside dark chrome, no dark surface inside a document, and
   that a portalled dropdown resolves dark.
4. `npm run typecheck` and `npm run build` clean.
5. The boot preloader screenshotted, confirming it does not flash a light
   background before the app paints.

## Files

**Modified**
- `frontend/src/index.css` - the `@theme` block, `.sheet-light`, shadows,
  `.well`'s border, `color-scheme`.
- `frontend/index.html` - the boot preloader's palette.
- `frontend/src/App.tsx` - `.sheet-light` on the quotation and proposal frames.
- `frontend/src/components/client/ClientShell.tsx` - `.sheet-light` on its root.
- `frontend/src/components/DesignEditor.tsx` - `.sheet-light` on the preview
  sheet.

**Untouched**
- All 54 components' colour utilities.
- Every backend file, every renderer, every PDF.
- `ProposalDesign` and the document design defaults.
