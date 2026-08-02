import { useRef } from 'react'
import type { KeyboardEvent } from 'react'

/**
 * The house switch, wearing proper tab semantics.
 *
 * Visually it is the segmented control the payment terms already use — a pill
 * on the secondary fill, the selected one lifted onto paper. What it adds is
 * the part a screen reader and a keyboard need: a real tablist, arrow keys
 * between the tabs, and one stop in the tab order rather than one per tab, so
 * Tab from the last tab lands in the panel instead of walking the strip.
 *
 * Panels are the caller's business. This only says which one is showing.
 */

type Tab = {
  id: string
  label: string
}

type TabStripProps = {
  tabs: Tab[]
  current: string
  onSelect: (id: string) => void
  label?: string
}

export default function TabStrip({ tabs, current, onSelect, label = 'Sections' }: TabStripProps) {
  const strip = useRef<HTMLDivElement | null>(null)

  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    // `| undefined` because any other key really is absent from this table,
    // which is what the guard on the next line already says.
    const keys: Record<string, number | undefined> = {
      ArrowRight: 1,
      ArrowDown: 1,
      ArrowLeft: -1,
      ArrowUp: -1,
    }
    const step = keys[event.key]
    if (!step) return
    event.preventDefault()

    const index = tabs.findIndex((tab) => tab.id === current)
    const next = tabs[(index + step + tabs.length) % tabs.length]
    onSelect(next.id)
    // Focus follows selection, which is what makes arrow keys feel like a
    // switch rather than a cursor.
    strip.current?.querySelector<HTMLElement>(`[data-tab="${next.id}"]`)?.focus()
  }

  return (
    <div
      ref={strip}
      role="tablist"
      aria-label={label}
      onKeyDown={move}
      className="flex gap-1 rounded-lg bg-duplicate p-1"
    >
      {tabs.map((tab) => {
        const selected = tab.id === current
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            data-tab={tab.id}
            id={`tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onSelect(tab.id)}
            className={`rounded-xs px-3 py-1.5 font-label text-[12px] uppercase tracking-[0.14em] ${
              selected ? 'bg-paper text-ink shadow-sheet' : 'text-void'
            }`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
