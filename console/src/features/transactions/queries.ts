import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type { Page_TransactionStaffView_, TransactionRevealedView, TransactionStaffView } from '@/api/types'
import { useAuth } from '@/auth/use-auth'
import { filtersToQuery, type TransactionFilters } from './filters'

// Every transaction query sits under this root. None of them holds plaintext: the list and the
// detail are the masked staff views, and the plaintext comes only from `useRevealTransaction`,
// which keeps nothing.
const TRANSACTIONS = 'transactions'

// `GET /v1/transactions` reads its filters as one query model, which the OpenAPI document
// describes as a single object parameter: sent as plain `key=value` pairs, as the API reads them.
const FLAT_QUERY = { object: { style: 'form', explode: true } } as const

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

/**
 * One page of the tenant's transactions, newest first; `cursor` is the previous page's
 * `next_cursor`. The console is staff, so the API answers with the staff views.
 */
export function useTransactions(filters: TransactionFilters, cursor?: string, { enabled = true } = {}) {
  const signedIn = useSignedIn()
  const query = filtersToQuery(filters)
  return useQuery({
    queryKey: [TRANSACTIONS, signedIn.tenant, 'list', cursor ?? null, query],
    queryFn: async () =>
      (await unwrap(
        api.GET('/transactions', {
          params: { query: { query, limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
          querySerializer: FLAT_QUERY,
        }),
      )) as Page_TransactionStaffView_,
    enabled: signedIn.enabled && enabled,
    placeholderData: keepPreviousData,
  })
}

/** One transaction, masked: the staff view, without `raw` or the counterparty's values. */
export function useTransaction(transactionId: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [TRANSACTIONS, tenant, 'one', transactionId],
    queryFn: async () =>
      (await unwrap(
        api.GET('/transactions/{transaction_id}', { params: { path: { transaction_id: transactionId } } }),
      )) as TransactionStaffView,
    enabled,
  })
}

/**
 * Reveal a transaction's payload and its counterparty. Each call is audited as `VIEW_PII`, so it
 * is a mutation the user starts with a button (never a query a refresh could repeat), it
 * invalidates nothing, and its answer is not kept once the page lets go of it (`gcTime: 0`).
 */
export function useRevealTransaction(transactionId: string) {
  return useMutation({
    mutationFn: async () =>
      (await unwrap(
        api.GET('/transactions/{transaction_id}', {
          params: { path: { transaction_id: transactionId }, query: { reveal: true } },
        }),
      )) as TransactionRevealedView,
    gcTime: 0,
  })
}
