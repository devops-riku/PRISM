/**
 * docs/DESIGN.md budgets the app's moments of motion — six now, and the count
 * is written into the MOTION heading in src/index.css — and requires all of
 * them to collapse under `prefers-reduced-motion: reduce`. Every one is
 * implemented in CSS inside a `no-preference` guard, so components do not
 * animate anything themselves.
 *
 * This is the one place the preference is READ, and everything that needs it
 * in JavaScript comes through here rather than writing the media query out
 * again. Two things do, both of them behaviours CSS cannot express:
 *
 *   - whether to scroll to a freshly prepared quotation smoothly or jump to it
 *     (App.tsx), and
 *   - whether a dashboard figure counts up to itself or is simply printed
 *     (lib/useCountUp.ts).
 */

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
