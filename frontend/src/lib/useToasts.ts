import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Short-lived messages that do not move the page.
 *
 * A sign-in card is the whole screen and every field on it is above the fold.
 * Growing an error paragraph inside it pushes the button the person was about
 * to press — so the message arrives and the target moves at the same moment.
 * A toast says the same thing without touching the layout.
 *
 * Deliberately small: no context, no provider, no global bus. A screen that
 * wants toasts holds this hook and renders the stack itself. That keeps the
 * lifetime of a message tied to the lifetime of the screen that raised it, so
 * navigating away takes its toasts with it rather than leaving them shouting
 * about a form nobody is looking at any more.
 */

export type ToastTone = 'alert' | 'note'

export type Toast = {
  id: number
  tone: ToastTone
  message: string
  hint: string
}

export type ToastRequest = {
  tone?: ToastTone
  message: string
  hint?: string
  /**
   * How long to stay, in milliseconds. Defaults to the tone's own lifetime.
   *
   * Overridden for a message that is an instruction rather than a
   * confirmation — "go and open your email" has to survive the walk to the
   * other tab.
   */
  duration?: number
}

/** How long each tone stays. An error is read twice; a confirmation is glanced at. */
const LIFETIME: Record<ToastTone, number> = {
  alert: 7000,
  note: 4500,
}

/** Three at once is a stack; four is a wall. The oldest leaves. */
const MAX_VISIBLE = 3

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id)
    if (timer !== undefined) {
      window.clearTimeout(timer)
      timers.current.delete(id)
    }
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const show = useCallback(
    ({ tone = 'note', message, hint = '', duration }: ToastRequest) => {
      if (!message.trim()) return 0

      const id = nextId.current
      nextId.current += 1

      setToasts((current) => [...current, { id, tone, message, hint }].slice(-MAX_VISIBLE))
      timers.current.set(
        id,
        window.setTimeout(() => dismiss(id), duration ?? LIFETIME[tone]),
      )
      return id
    },
    [dismiss],
  )

  /** Take everything down at once — used when a screen changes under them. */
  const clear = useCallback(() => {
    timers.current.forEach((timer) => window.clearTimeout(timer))
    timers.current.clear()
    setToasts([])
  }, [])

  // A timer that fires after unmount would call setState on a dead component.
  // The map is the only thing holding them, so emptying it is the whole job.
  useEffect(() => {
    const pending = timers.current
    return () => {
      pending.forEach((timer) => window.clearTimeout(timer))
      pending.clear()
    }
  }, [])

  return { toasts, show, dismiss, clear }
}
