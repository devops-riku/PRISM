/**
 * docs/DESIGN.md allows four moments of motion and requires all of them to
 * collapse under `prefers-reduced-motion: reduce`. All four are implemented in
 * CSS in src/index.css, inside a `no-preference` guard — so components do not
 * animate anything themselves.
 *
 * This is the one place the preference is read in JavaScript, for the single
 * behaviour CSS cannot express: whether to scroll to a freshly prepared
 * quotation smoothly or jump to it.
 */

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
