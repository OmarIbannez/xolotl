import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type { CounterpartyScreeningStatus } from '@/api/types'
import { useAuth } from '@/auth/use-auth'

// The list and the detail are masked (initials and last-four tails); the plaintext comes only
// from `useRevealCounterparty`, which keeps nothing.
const COUNTERPARTIES = 'counterparties'

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

/** One page of counterparties, newest last sighting first, optionally narrowed to one status. */
export function useCounterparties(status: CounterpartyScreeningStatus | undefined, cursor?: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [COUNTERPARTIES, tenant, 'list', cursor ?? null, status ?? null],
    queryFn: () =>
      unwrap(
        api.GET('/counterparties', {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(cursor === undefined ? {} : { cursor }),
              ...(status === undefined ? {} : { status }),
            },
          },
        }),
      ),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** One counterparty, masked, with its findings and its last runs. */
export function useCounterparty(counterpartyId: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [COUNTERPARTIES, tenant, 'detail', counterpartyId],
    queryFn: () =>
      unwrap(
        api.GET('/counterparties/{counterparty_id}', { params: { path: { counterparty_id: counterpartyId } } }),
      ),
    enabled,
  })
}

/**
 * Reveal a counterparty's name, account, wallet and identifier. Each call is audited as
 * `VIEW_PII` (one row per value), so it is a mutation the user starts with a button, never a
 * query a refresh could repeat, and its answer is not kept once the card lets go of it.
 */
export function useRevealCounterparty(counterpartyId: string) {
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.GET('/counterparties/{counterparty_id}/reveal', {
          params: { path: { counterparty_id: counterpartyId } },
        }),
      ),
    gcTime: 0,
  })
}
