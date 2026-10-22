import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, unwrapReplayable } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type {
  CaptureLimitView,
  CapturePeriodStatus,
  Page_CapturePeriodResponse_,
  Page_ReportableEventSummary_,
  ReportableEventStatus,
} from '@/api/types'
import { useIdempotentMutation } from '@/api/use-idempotent-mutation'
import { useAuth } from '@/auth/use-auth'

// Every captures query sits under this root. Captures are staff only (a capture says a customer is
// being reported); they hold ids, codes and amounts, never a name.
const CAPTURES = 'captures'

// The list routes read their filters as one query model, which the OpenAPI document describes as
// a single object parameter: sent as plain `key=value` pairs, as the API reads them.
const FLAT_QUERY = { object: { style: 'form', explode: true } } as const

/** What the periods are narrowed to; unset keys are left out of the request. */
export interface PeriodFilters {
  capture_code?: string
  status?: CapturePeriodStatus
}

/** What the captures list is narrowed to; unset keys are left out of the request. */
export interface CaptureFilters {
  capture_code?: string
  report_code?: string
  period_key?: string
  status?: ReportableEventStatus
  relationship_id?: string
  transaction_id?: string
}

/** Drop the keys left unset, so the request and the cache key carry only real filters. */
function compact<T extends object>(filters: T): T {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined)) as T
}

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

/**
 * The captures and tier limits of the policy in force on `on` (a Mexico City day) for `product`
 * (tenant-wide when unset); 404 `POLICY_NOT_ASSIGNED` when none is.
 */
export function useEffectiveCaptures(on: string, product?: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [CAPTURES, tenant, 'effective', on, product ?? null],
    queryFn: async () =>
      (await unwrap(
        api.GET('/monitoring/captures/effective', {
          params: { query: { on, ...(product === undefined ? {} : { product }) } },
        }),
      )) as CaptureLimitView[],
    enabled,
  })
}

/** One page of the capture periods, the latest period key first; the cursor is opaque. */
export function useCapturePeriods(filters: PeriodFilters, cursor?: string) {
  const signedIn = useSignedIn()
  const query = compact(filters)
  return useQuery({
    queryKey: [CAPTURES, signedIn.tenant, 'periods', cursor ?? null, query],
    queryFn: async () =>
      (await unwrap(
        api.GET('/monitoring/captures/periods', {
          params: { query: { query, limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
          querySerializer: FLAT_QUERY,
        }),
      )) as Page_CapturePeriodResponse_,
    enabled: signedIn.enabled,
    placeholderData: keepPreviousData,
  })
}

/** One page of the reportable events, newest first; `cursor` is the previous page's `next_cursor`. */
export function useCaptures(filters: CaptureFilters, cursor?: string) {
  const signedIn = useSignedIn()
  const query = compact(filters)
  return useQuery({
    queryKey: [CAPTURES, signedIn.tenant, 'list', cursor ?? null, query],
    queryFn: async () =>
      (await unwrap(
        api.GET('/monitoring/captures', {
          params: { query: { query, limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
          querySerializer: FLAT_QUERY,
        }),
      )) as Page_ReportableEventSummary_,
    enabled: signedIn.enabled,
    placeholderData: keepPreviousData,
  })
}

/** The events a transaction was captured as (one page: a transaction has a few at most). */
export function useTransactionCaptures(transactionId: string) {
  return useCaptures({ transaction_id: transactionId })
}

/**
 * Close a period now (MLRO or ADMIN; 409 `PERIOD_NOT_ENDED` before the day after its last day).
 * The close reconciles the period and may void or restore events, so the periods and the
 * captures are read again.
 */
export function useClosePeriod() {
  const qc = useQueryClient()
  const { tenant } = useSignedIn()
  return useIdempotentMutation({
    mutationFn: (periodId: string, idempotencyKey: string) =>
      unwrapReplayable(
        api.POST('/monitoring/captures/periods/{period_id}/close', {
          params: { path: { period_id: periodId } },
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [CAPTURES, tenant, 'periods'] })
      void qc.invalidateQueries({ queryKey: [CAPTURES, tenant, 'list'] })
    },
  })
}
