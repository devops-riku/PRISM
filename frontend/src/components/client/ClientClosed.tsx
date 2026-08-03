import { useEffect, useRef } from 'react'
import { DISPLAY } from '../tokens'

/**
 * Face 4: the one page a closed intake, an expired link, an unrecognised
 * token and a link that never existed all answer with - deliberately
 * identical, and deliberately thin. See `_CLIENT_LINK_GONE` in
 * `backend/app/main.py` and `ClientApiError.isGone`'s own docstring: every
 * refusal on the client's door is the same opaque 404, on purpose, so a
 * stranger holding a wrong or withdrawn link cannot learn anything about
 * what it used to be.
 *
 * No eyebrow naming a studio - the `closed` state itself carries no
 * `studio_name` (`clientview.of` returns only `{"state": "closed"}` for it,
 * and the `gone` shell status carries nothing at all), so "the studio's
 * name is the headline on every face" correctly stops applying here. No
 * wording like "not open right now" or "this link has expired" either: both
 * confirm a link once worked, which is exactly what this page must never
 * do. One flat sentence, true of a wrong guess and a withdrawn request
 * alike.
 */
export default function ClientClosed() {
  // This face can arrive as a transition - a write mid-session discovers the
  // link is gone and ClientShell swaps whatever was on screen for this - as
  // easily as a fresh load. The heading takes focus either way, the same
  // treatment every other face gives its own: a screen reader hears
  // "There's nothing here" immediately rather than needing to explore the
  // page to find out anything happened at all.
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  useEffect(() => {
    headingRef.current?.focus()
  }, [])

  return (
    <div className="rounded-[18px] border border-rule bg-paper p-7 text-center shadow-raised sm:p-8">
      <h1
        ref={headingRef}
        tabIndex={-1}
        className={`${DISPLAY} focus-landing text-[19px] leading-[1.4] text-ink`}
      >
        There&rsquo;s nothing here.
      </h1>
    </div>
  )
}
