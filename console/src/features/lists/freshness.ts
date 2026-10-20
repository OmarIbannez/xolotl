import type { ListOverview } from '@/api/types'

/** How current a subscribed list is, as its badge shows it. */
export type Freshness = 'FRESH' | 'STALE' | 'FAILING' | 'EMPTY'

/**
 * No active version yet is EMPTY; a failing last fetch is FAILING (even when the version is
 * also stale, since the failure is the reason); a version older than twice the poll interval
 * is STALE; anything else is FRESH.
 */
export function freshnessOf(row: ListOverview): Freshness {
  if (row.active_seq === null) return 'EMPTY'
  if (row.failing) return 'FAILING'
  if (row.stale) return 'STALE'
  return 'FRESH'
}

/**
 * The stable code of a source's `last_error` (`HTTP_503`, `LIST_MASS_REMOVAL: removed 412 of
 * 1003` becomes `LIST_MASS_REMOVAL`), or null when there is none.
 */
export function errorCodeOf(lastError: string | null): string | null {
  const code = lastError?.split(':', 1)[0]?.trim()
  return code ? code : null
}
