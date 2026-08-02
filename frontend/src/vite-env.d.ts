/// <reference types="vite/client" />

/**
 * The boot screen's own API, defined by the inline script in index.html before
 * this bundle is fetched. Optional on purpose: `index.html` is the one file a
 * deployment can replace, and a missing boot screen must not stop the app from
 * starting — every call site uses `?.`.
 */
declare global {
  interface Window {
    __prismBoot?: {
      /** How far the load has genuinely got: 1 downloaded, 2 started. */
      step: (reached: number) => void
      /** The app is up; take the screen down. */
      done: () => void
    }
  }
}

export {}
