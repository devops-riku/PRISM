/**
 * What happened, and the next move. No apology, no stack trace, no shrug.
 *
 * A banner rather than a panel: an error is news, and news that cannot be put
 * down keeps the thing it is about off the screen. It states the problem in one
 * line, the remedy in the next, and closes - the failure is already in the
 * jobs page and in the notifications, so nothing is lost by dismissing it here.
 */

/**
 * The three parts of App's `describeError`, plus the way to close the banner.
 * `code` is the HTTP status when there was one and `null` when the failure
 * never reached the API.
 */
export type ErrorNoticeProps = {
  headline: string
  next?: string
  code?: number | null
  onDismiss?: () => void
}

export default function ErrorNotice({ headline, next, code, onDismiss }: ErrorNoticeProps) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-alert/40 bg-paper px-4 py-3"
    >
      <span
        aria-hidden="true"
        className="mt-[7px] h-1.5 w-1.5 flex-none rounded-pill bg-alert"
      />

      <div className="min-w-0 flex-1">
        <p className="font-body text-[14px] leading-snug text-ink">
          {headline}
          {code ? <span className="text-faint"> · {code}</span> : null}
        </p>
        {next ? (
          <p className="mt-0.5 font-body text-[13px] leading-[1.5] text-void">{next}</p>
        ) : null}
      </div>

      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss this message"
          className="-mr-1 -mt-1 flex h-7 w-7 flex-none items-center justify-center rounded-md text-faint transition-colors duration-150 hover:bg-duplicate hover:text-ink"
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
      ) : null}
    </div>
  )
}
