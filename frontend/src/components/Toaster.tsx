import type { Toast } from '../lib/useToasts'

/**
 * The toast stack: bottom of the screen, above everything, touching nothing.
 *
 * Fixed rather than absolute, so it does not care what the screen underneath is
 * doing — a card that scrolls, a dialog that opens, a form that grows. The
 * layout below never shifts because a message arrived.
 *
 * An alert is `role="alert"`, which a screen reader announces immediately; a
 * note is `role="status"`, which waits for a pause. That is the actual
 * difference between "your password was wrong" and "check your inbox", and it
 * is the only thing the tone changes besides colour and how long it stays.
 *
 * The animation is `.toast` in index.css, inside the reduced-motion guard with
 * the other four — no component in this project animates anything itself.
 */

type ToasterProps = {
  toasts: Toast[]
  onDismiss: (id: number) => void
}

export default function Toaster({ toasts, onDismiss }: ToasterProps) {
  if (!toasts.length) return null

  return (
    <div
      // `pointer-events-none` on the rail, restored on each toast: the empty
      // space beside a toast must not swallow a click meant for the page.
      className="pointer-events-none fixed inset-x-0 bottom-0 z-[80] flex flex-col items-center gap-2 px-4 pb-4 sm:items-end sm:px-6 sm:pb-6"
    >
      {toasts.map((toast) => {
        const alert = toast.tone === 'alert'
        return (
          <div
            key={toast.id}
            role={alert ? 'alert' : 'status'}
            className={`toast pointer-events-auto flex w-full max-w-[26rem] items-start gap-3 rounded-xl border px-4 py-3 shadow-sheet ${
              alert ? 'border-alert/30 bg-alert-soft' : 'border-rule bg-paper'
            }`}
          >
            <span
              aria-hidden="true"
              className={`mt-[7px] h-1.5 w-1.5 flex-none rounded-full ${
                alert ? 'bg-alert' : 'bg-ballpoint'
              }`}
            />

            <div className="min-w-0 flex-1">
              <p className="font-body text-[14px] leading-[1.5] text-ink">{toast.message}</p>
              {toast.hint ? (
                <p className="mt-0.5 font-body text-[13px] leading-[1.5] text-void">{toast.hint}</p>
              ) : null}
            </div>

            <button
              type="button"
              onClick={() => onDismiss(toast.id)}
              aria-label="Dismiss"
              className="-mr-1 -mt-1 flex h-7 w-7 flex-none items-center justify-center rounded-md text-void transition-colors duration-150 hover:bg-hairline/60 hover:text-ink"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 14 14"
                className="h-3 w-3"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              >
                <path d="M2 2l10 10M12 2L2 12" />
              </svg>
            </button>
          </div>
        )
      })}
    </div>
  )
}
