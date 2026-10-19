import type { Location } from 'react-router-dom'

/** Router state that remembers where a visitor was headed before a guard intervened. */
export interface RedirectState {
  from: string
}

/** The path and query of a location, as stored in `RedirectState.from`. */
export function pathOf(location: Pick<Location, 'pathname' | 'search'>): string {
  return `${location.pathname}${location.search}`
}

const PRE_SESSION_PATHS = ['/login', '/setup-password']

/**
 * Where to go once the guards are satisfied: the remembered path when it is a page of
 * this console (never another origin, never a pre-session screen), else the home page.
 */
export function redirectTarget(state: unknown): string {
  if (typeof state !== 'object' || state === null || !('from' in state)) return '/'
  const { from } = state
  if (typeof from !== 'string' || !from.startsWith('/') || from.startsWith('//')) return '/'
  const path = from.split(/[?#]/, 1)[0] ?? from
  return PRE_SESSION_PATHS.includes(path) ? '/' : from
}
