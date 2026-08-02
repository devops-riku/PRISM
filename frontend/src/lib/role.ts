import { useEffect, useState } from 'react'
import { fetchTeam } from './api'
import type { MemberRole } from '../types'

/**
 * What you may do in the workspace you have open.
 *
 * The server decides and the server enforces — a member who forges a request
 * gets a 403, not a surprise. This exists so the interface does not offer what
 * would be refused: a Delete that always fails is worse than no Delete, because
 * it teaches somebody the app is broken rather than that the door is locked.
 *
 * Read once per page load and shared, since it changes about as often as
 * somebody is promoted.
 */

let cached: MemberRole | null = null
let inflight: Promise<MemberRole> | null = null
const listeners = new Set<(role: MemberRole | null) => void>()

export function roleNow(): MemberRole | null {
  return cached
}

export async function loadRole(): Promise<MemberRole> {
  if (cached) return cached
  if (!inflight) {
    inflight = fetchTeam()
      .then((team) => {
        cached = team?.your_role || 'admin'
        return cached
      })
      // An install with no accounts has no team, and everybody can do
      // everything - which is what it did before roles existed.
      .catch(() => {
        cached = 'admin'
        return cached
      })
      .finally(() => {
        inflight = null
        listeners.forEach((listen) => listen(cached))
      })
  }
  return inflight
}

/** Forget it — after switching workspace, where the answer is different. */
export function forgetRole(): void {
  cached = null
}

export function useRole(): { role: MemberRole | null; isAdmin: boolean } {
  const [role, setRole] = useState<MemberRole | null>(cached)

  useEffect(() => {
    let live = true
    loadRole().then((found) => live && setRole(found))
    const listen = (found: MemberRole | null) => live && setRole(found)
    listeners.add(listen)
    return () => {
      live = false
      listeners.delete(listen)
    }
  }, [])

  return { role, isAdmin: role !== 'member' }
}
