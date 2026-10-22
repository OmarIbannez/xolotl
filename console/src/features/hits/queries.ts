import { keepPreviousData, useMutation, useQueries, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type { HitDetail, HitOutcome, HitReveal, Page_HitSummary_, Severity } from '@/api/types'
import { useAuth } from '@/auth/use-auth'

// Every hit query sits under this root. None of them holds plaintext: the queue, the detail and
// its transactions are masked, and the plaintext comes only from `useRevealHit`, which keeps nothing.
const HITS = 'hits'

// `GET /v1/monitoring/hits` reads its filters as one query model, which the OpenAPI document
// describes as a single object parameter: sent as plain `key=value` pairs, as the API reads them.
const FLAT_QUERY = { object: { style: 'form', explode: true } } as const

/** The queue's tabs, the most severe first. */
export const SEVERITIES: readonly Severity[] = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

/** What the queue is narrowed to; unset keys are left out of the request. */
export interface HitFilters {
  severity?: Severity
  rule_code?: string
  typology?: string
  outcome?: HitOutcome
  party_id?: string
  transaction_id?: string
  /** An instant: only hits created before it (the "older than" filter). */
  created_before?: string
}

/** Drop the keys left unset, so the request and the cache key carry only real filters. */
function compact(filters: HitFilters): HitFilters {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined)) as HitFilters
}

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

function listHits(filters: HitFilters, limit: number, cursor: string | undefined) {
  return unwrap(
    api.GET('/monitoring/hits', {
      params: { query: { query: filters, limit, ...(cursor === undefined ? {} : { cursor }) } },
      querySerializer: FLAT_QUERY,
    }),
  ) as Promise<Page_HitSummary_>
}

/** One page of the hits, newest first; `cursor` is the previous page's `next_cursor`. */
export function useHits(filters: HitFilters, cursor?: string, { enabled = true } = {}) {
  const signedIn = useSignedIn()
  const query = compact(filters)
  return useQuery({
    queryKey: [HITS, signedIn.tenant, 'list', cursor ?? null, query],
    queryFn: () => listHits(query, PAGE_SIZE, cursor),
    enabled: signedIn.enabled && enabled,
    placeholderData: keepPreviousData,
  })
}

/**
 * Which severities have a hit under `filters`: one single-row request per severity, so the queue
 * can open on the most severe tab that is not empty. Undefined until every probe has answered.
 */
export function useSeveritiesWithHits(filters: Omit<HitFilters, 'severity'>): readonly Severity[] | undefined {
  const { tenant, enabled } = useSignedIn()
  const query = compact(filters)
  return useQueries({
    queries: SEVERITIES.map((severity) => ({
      queryKey: [HITS, tenant, 'any', severity, query],
      queryFn: () => listHits({ ...query, severity }, 1, undefined),
      enabled,
    })),
    combine: (results) =>
      // A probe that failed counts as empty: the queue then opens and shows its own error.
      results.every((result) => result.data !== undefined || result.isError)
        ? SEVERITIES.filter((_, index) => (results[index]?.data?.items.length ?? 0) > 0)
        : undefined,
  })
}

/** One hit, masked: its evidence, the rendered sentence and its newest linked transactions. */
export function useHit(hitId: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [HITS, tenant, 'one', hitId],
    queryFn: async () =>
      (await unwrap(api.GET('/monitoring/hits/{hit_id}', { params: { path: { hit_id: hitId } } }))) as HitDetail,
    enabled,
  })
}

/**
 * One page of every transaction linked to a hit, newest first. The detail carries only the
 * newest links, so this is read only when the hit has more (`enabled`).
 */
export function useHitTransactions(hitId: string, cursor: string | undefined, { enabled = true } = {}) {
  const signedIn = useSignedIn()
  return useQuery({
    queryKey: [HITS, signedIn.tenant, 'transactions', hitId, cursor ?? null],
    queryFn: () =>
      unwrap(
        api.GET('/monitoring/hits/{hit_id}/transactions', {
          params: { path: { hit_id: hitId }, query: { limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
        }),
      ),
    enabled: signedIn.enabled && enabled,
    placeholderData: keepPreviousData,
  })
}

/**
 * Reveal a hit's party and the counterparties of its shown transactions. Each call is audited as
 * `VIEW_PII`, so it is a mutation the user starts with a button (never a query a refresh could
 * repeat), it invalidates nothing, and its answer is not kept once the card lets go of it.
 */
export function useRevealHit(hitId: string) {
  return useMutation({
    mutationFn: async () =>
      (await unwrap(
        api.GET('/monitoring/hits/{hit_id}/reveal', {
          params: { path: { hit_id: hitId } },
          // Plaintext: never kept by the browser's HTTP cache either, whatever the server's headers.
          cache: 'no-store',
        }),
      )) as HitReveal,
    gcTime: 0,
  })
}

/** One page of a party's hits, newest first (404 for a party the tenant does not have). */
export function usePartyHits(partyId: string, cursor: string | undefined) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [HITS, tenant, 'party', partyId, cursor ?? null],
    queryFn: () =>
      unwrap(
        api.GET('/parties/{party_id}/hits', {
          params: { path: { party_id: partyId }, query: { limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
        }),
      ),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** The hits a transaction is linked to, in any role (one page: a transaction has few). */
export function useTransactionHits(transactionId: string) {
  return useHits({ transaction_id: transactionId })
}
