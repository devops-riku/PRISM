# PRISM — design direction: Clarity Kit

Supplied by the client as `Desktop/Design Framework/Clarity Kit.dc.html`. It is
the house style, not a starting point. Follow it; do not substitute taste.

This replaces the earlier "Duplicate Copy" carbon-pad direction entirely. Where
the two disagree, this wins.

## The idea

> One screen, one thing at a time.

A small, warm kit for products with no navigation bar. The interface asks one
clear question, keeps what has been answered in view, and gets out of the way.
Calm, not clinical: warm paper tones, generous radii, soft shadows, one deep
green that carries every action.

For PRISM that fits almost exactly. The pad already is one screen — a brief in,
two documents out, no chat loop. What changes is the register: the old direction
was a carbon-copy quotation pad, all hard rules and ballpoint. This one is quiet
and modern, and lets the numbers be the loudest thing on the page.

## Colour — the kit's palette, verbatim

```
canvas        #F6F3EE   the page itself, warm off-white
surface       #FFFDFA   cards, sheets, inputs
raise         #F1EDE5   secondary fill: wells, chips, table stripes
line          #E4DED4   borders
line-soft     #EFEAE2   dividers inside a card
ink           #1D1B17   headings
body          #4A443B   body copy
muted         #6B6459   secondary copy
faint         #8A8378   eyebrows, captions, table headers
accent        #35655A   the one action colour, a deep pine green
accent-deep   #24463E   hover
accent-soft   #EDF1EF   accent tint background
alert         #A8443A   the single warm red, for a real problem only
alert-soft    #FCF4F2   its tint
```

Light only. The accent is never a large fill except on the primary button.

## Type

One family. The kit links Instrument Serif but applies it nowhere, so neither
does PRISM.

```
Instrument Sans    400 / 500 / 600
                   Everything: headings, body, labels, figures.
                   Headings 600 with tight tracking (-0.01em, -0.035em at hero).
                   Labels 12-13px UPPERCASE, letter-spacing 0.14em, faint.
                   Display figures 600 at -0.03em — the kit's own treatment for
                   a headline number ("1 person", "2 minutes").
```

Every figure carries `font-variant-numeric: tabular-nums`, in a table or out of
one, so a column of money always aligns.

Scale: 12 / 13 / 14 / 15 / 16 / 18 / 20 / 24 / 26 / 34 / 58. Body 15px at 1.6.

## Shape and depth

```
radius   7px chips · 11px buttons and controls · 12px inputs · 14px cards
         18-22px large panels · 99px pills and progress
shadow   card    0 1px 2px rgba(40,33,22,.04), 0 18px 40px -28px rgba(40,33,22,.16)
         raised  0 10px 24px -18px rgba(40,33,22,.3)
         button  0 8px 18px -12px rgba(53,101,90,.9)
         focus   0 0 0 4px rgba(53,101,90,.12)   ← plus a 1px accent border
```

Inputs are `surface` with a 1px `line` border and a 12px radius, and on focus the
border turns accent with the 4px tinted ring. Nothing reflows on focus.

## Layout

Single column, max-width 1180px for the admin ledger and 940px for the pad and
the documents. Sections are separated by 88px of air, with a header row above a
1px `line` rule.

Section headers follow the kit exactly: a small uppercase tracked eyebrow
(`01 — The pattern`) over a 26px/600 title. PRISM's form rows genuinely are a
numbered sequence, so the numbering is honest here rather than decorative.

The two results stop being an original and a carbon duplicate. They become two
cards: the client proposal on `surface`, the developer requirements on `raise`
with an accent left edge. Same idea — one document each for two readers — in the
kit's language instead of the pad's.

## Motion

The kit ships four keyframes and they are the whole budget:

```
ck-rise      opacity 0 → 1, translateY(10px) → 0      results arriving
ck-toast     opacity 0 → 1, translateY(14px) scale(.98) → 1   the total landing
ck-shimmer   background-position -300px → 300px       loading placeholders
ck-spin      rotate(360deg)                           the working indicator
```

Transitions are `.15s ease` for colour and border, and
`.5s cubic-bezier(.2,.7,.2,1)` for the progress thread. Everything sits behind
`@media (prefers-reduced-motion: no-preference)`.

The progress thread — a 2px accent bar across the top of the card, widening as
the work proceeds — is the kit's own device and replaces the old stage ticker's
job of showing that something is happening.

## Copy

Unchanged from before, and consistent with the kit's voice: sentence case,
plain verbs, an action keeps its name through the flow. Errors say what happened
and what to do next, and never apologise. An empty screen is an invitation.
