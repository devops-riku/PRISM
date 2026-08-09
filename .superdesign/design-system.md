# PRISM design system

## Product and route context

PRISM turns one project scope into two consistent outputs: a priced client quotation and a structured requirements sheet. The public landing page belongs at the clean `/` URL. The authenticated studio remains a private hash-routed application, and token-bearing client/invitation hashes must never become indexable marketing routes.

The landing page has three jobs, in order:

1. Explain the input-to-two-outputs idea in one glance.
2. Show that PRISM grounds the quotation in the studio's rate card, policies, and scope.
3. Offer a clear path to sign in or open the studio without letting authentication dominate the product story.

## Brand idea

The brand mark is a prism: one line enters, two useful documents leave. Use the real rounded-tile `PrismMark` from `frontend/src/components/PrismMark.tsx`; never substitute a generic triangle logo. The spectrum dashes remain teal `#1b98a8`, amber `#e3ae3c`, and coral `#d9645e` in every theme.

For this landing redesign, the supplied line-art diagram is the primary visual language. It should be rebuilt as crisp responsive SVG/CSS geometry, not treated as a decorative bitmap. The left label must read **YOUR SCOPE**. The output labels are **CLIENT QUOTATION** and **REQUIREMENTS SHEET**. Lines need visible endpoints and must clearly pass through a central outlined prism.

## Visual direction

Follow the existing Clarity Kit: calm, warm, precise, and editorial rather than loud SaaS marketing. One screen should communicate one idea. Use generous negative space, restrained line work, and small tracked labels. No gradients, glassmorphism, neon, stock illustrations, fake dashboard screenshots, or invented colors.

The public landing page is light-first, matching the supplied reference:

- Canvas: `#faf7f2`
- Surface: `#fffdf9`
- Secondary surface: `#f1ebe1`
- Border: `#e1d9cc`
- Hairline: `#ece6dc`
- Heading ink: `#1c1815`
- Body: `#3e3830`
- Secondary copy: `#605850`
- Faint labels: `#776e63`
- Primary pine action: `#55631f`
- Pine hover: `#414c13`
- Pine tint: `#eef1de`
- Logo tile / mark in light mode: `#e2eae4` / `#14392c`

The private studio may continue using its current warm dark palette; do not redesign the authenticated application as part of the landing task.

## Typography

- Display and tracked labels: Instrument Sans, falling back to Figtree and system sans.
- Body: Figtree, falling back to system sans.
- Headlines: weight 600, sentence case, tight tracking around `-0.01em` to `-0.03em`.
- Body: 15–16px with roughly 1.6 line height.
- Diagram and eyebrow labels: 12–13px uppercase with `0.14em` tracking.
- Use tabular figures wherever numbers appear.

## Shape, spacing, and depth

- Controls: 11–12px radii.
- Cards: 14px; large panels: 18–22px.
- Prefer borders and whitespace to heavy elevation.
- If a card is needed, use the existing soft sheet/raised shadows only.
- Use the existing maximum app width of 1400px; retain at least 24px side padding on small screens and 56–72px on large screens.
- The reference diagram should feel broad and architectural on desktop, not squeezed into a card.

## Interaction and motion

- Color/border transitions: about 150ms.
- Large arrival motion: `cubic-bezier(0.2, 0.7, 0.2, 1)`.
- A line-draw or refracted-ray reveal is allowed if subtle and truthful to the diagram.
- Respect `prefers-reduced-motion`; in that mode the complete diagram is visible immediately.
- The landing must remain usable with keyboard navigation and visible focus states.

## Responsive behavior

- Desktop: preserve the reference's horizontal story—scope on the left, prism centered, two outputs on the right—with enough vertical room for both output labels.
- Tablet: keep the horizontal relationship but shorten rays and reduce label offsets.
- Mobile: recompose the same causal story vertically or diagonally; do not scale the desktop diagram until labels become unreadable or leave the viewport.
- The sign-in/open-studio action must remain reachable without overlapping the diagram.

## Copy and truthfulness

Keep copy short and code-backed. Do not invent customer counts, speed claims, ratings, pricing, or integrations. Preferred core statement: one project scope becomes a priced client quotation and a structured requirements sheet. Use **Your Scope**, never **Your Brief**, in the diagram.

## Fidelity constraint

Use only the fonts, colors, spacing, radii, shadows, and component styles defined here and in `frontend/src/index.css`. Reuse the real PRISM mark. Do not introduce any fonts, colors, gradients, or visual styles outside this design system.
