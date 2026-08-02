import TeamPanel from './TeamPanel'
import { CARD, DISPLAY } from './tokens'

/**
 * Who is on the workspace you have open.
 *
 * A page of its own rather than a panel under the workspace list, because they
 * answer different questions. The list is "which book of work am I in"; this is
 * "who else can see it". Stacking them meant every trip to change a workspace
 * ended in a roster nobody had asked for — and every trip to invite somebody
 * started with a list they had to scroll past.
 *
 * Scoped to the current workspace, always. There is no cross-workspace view of
 * people here, because membership is per workspace on the server and a merged
 * list would imply otherwise.
 */
export default function TeamScreen() {
  return (
    <section aria-labelledby="team-title" className={`${CARD} shrink-0`}>
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-rule px-5 py-3 sm:px-6">
        <h2 id="team-title" className={`${DISPLAY} text-[20px]`}>
          Teams
        </h2>
      </div>

      <div className="px-5 py-5 sm:px-6">
        <TeamPanel />
      </div>
    </section>
  )
}
