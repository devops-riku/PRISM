/**
 * The pad's step rail: where you are, what you have already said, and one click
 * back to any of it.
 *
 * The Clarity Kit's flow pattern is explicit that progress is felt through "the
 * hairline thread and the answers piling up beside you" — no counters, no
 * "3 of 5". So this shows the steps by name with the answer written underneath
 * each one, and the thread beside the current step carries the accent. A person
 * filling this in knows where they are because they can read what they said,
 * not because a number told them.
 *
 * Every step is reachable at any time. This is a pad, not a wizard: the scope
 * is the only thing PRISM genuinely needs, and gating the rest behind an order
 * would invent a dependency the form does not have.
 */

/** One step as the rail shows it: what it is called, and what has been said. */
export type RailStep = {
  id: string
  label: string
  placeholder: string
  answer: string
}

type StepRailProps = {
  steps: RailStep[]
  current: number
  onGo: (index: number) => void
  disabled?: boolean
}

export default function StepRail({ steps, current, onGo, disabled }: StepRailProps) {
  const answered = steps.filter((step) => step.answer).length

  return (
    <nav aria-label="Sections of the quotation" className="flex flex-col gap-0.5">
      <p className="px-3 pb-1.5 font-label text-[11px] uppercase tracking-[0.14em] text-faint">
        So far
      </p>
      {/* The empty state is worth one line, not three: seven steps have to fit
          the card beside a panel, and a rail that scrolls is a rail whose last
          entry - the one that finishes the flow - nobody can see. */}
      {answered === 0 ? (
        <p className="px-3 pb-2 font-body text-[12px] leading-[1.5] text-faint">
          Nothing yet. What you tell us shows up here.
        </p>
      ) : null}
      {steps.map((step, index) => {
        const active = index === current
        return (
          <button
            key={step.id}
            type="button"
            disabled={disabled}
            aria-current={active ? 'step' : undefined}
            onClick={() => onGo(index)}
            className={`group block w-full rounded-r-md border-l-2 py-1.5 pl-3 pr-2 text-left transition-[background-color,border-color] duration-150 ${
              active
                ? 'border-l-ballpoint bg-paper'
                : 'border-l-hairline hover:border-l-rule hover:bg-paper/70 disabled:hover:border-l-hairline disabled:hover:bg-transparent'
            }`}
          >
            <span
              className={`block truncate font-body text-[13.5px] leading-[1.35] ${
                active ? 'font-medium text-ink' : 'text-body'
              }`}
            >
              {step.label}
            </span>
            <span
              className={`block truncate font-body text-[12px] leading-[1.35] ${
                step.answer ? 'text-void' : 'text-faint'
              }`}
            >
              {step.answer || step.placeholder}
            </span>
          </button>
        )
      })}
    </nav>
  )
}
