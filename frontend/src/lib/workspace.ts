/**
 * Which book of work this browser is reading.
 *
 * A leaf on purpose. It used to live in `api.ts`, which is fine until signing
 * out needs to forget the workspace too — `api.ts` already imports `auth.ts`,
 * so reaching back the other way would make a cycle between the two modules the
 * whole app loads first. Two importers, no dependencies, no cycle.
 *
 * There is no login behind this value. It decides which book you are reading,
 * not what you are allowed to read: the server checks the roster on every
 * request and answers 403 for a workspace you are not on.
 */

const WORKSPACE_KEY = 'prism.workspace'

export function currentWorkspace(): string {
  try {
    return window.localStorage.getItem(WORKSPACE_KEY) || ''
  } catch {
    // Private browsing, or storage disabled. The server falls back to the first
    // workspace, which is a working app rather than a broken one.
    return ''
  }
}

export function setCurrentWorkspace(id: string): string {
  const value = String(id ?? '').trim()
  try {
    if (value) window.localStorage.setItem(WORKSPACE_KEY, value)
    else window.localStorage.removeItem(WORKSPACE_KEY)
  } catch {
    /* nothing to do - the header is simply omitted */
  }
  return value
}
