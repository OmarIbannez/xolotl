import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, unwrapReplayable, type Replayable } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type {
  IdentifierType,
  LegalHoldRequest,
  PartyCreateRequest,
  PartyResponse,
  PartyStaffResponse,
  PartyStatus,
} from '@/api/types'
import { useIdempotentMutation } from '@/api/use-idempotent-mutation'
import { useAuth } from '@/auth/use-auth'

// Every party query sits under this root, so a write can invalidate the list and the details at once.
// None of them holds plaintext personal data: the API masks lists and details for everyone.
const PARTIES = 'parties'

/** What the list is narrowed to. An identifier search sends `id_type` and `value` together. */
export interface PartyFilters {
  status?: PartyStatus
  external_ref?: string
  id_type?: IdentifierType
  value?: string
}

/** The staff shape of a party's detail embeds its sub-collections; the machine shape does not. */
export function isStaffView(p: PartyStaffResponse | PartyResponse): p is PartyStaffResponse {
  return 'contacts' in p
}

/** One page of the tenant's parties, newest first, masked; `cursor` is the previous page's `next_cursor`. */
export function useParties(filters: PartyFilters, cursor?: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [PARTIES, user?.tenant_id, cursor ?? null, filters],
    queryFn: () =>
      unwrap(api.GET('/parties', { params: { query: { limit: PAGE_SIZE, cursor, ...filters } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/** One party, masked (its plaintext only comes from `useRevealParty`). */
export function useParty(partyId: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [PARTIES, 'one', user?.tenant_id, partyId],
    queryFn: () => unwrap(api.GET('/parties/{party_id}', { params: { path: { party_id: partyId } } })),
    enabled: user !== undefined && !mustChangePassword,
  })
}

/**
 * Reveal a party's personal data. Each call is audited as `VIEW_PII`, so it is a mutation the
 * user starts with a button (never a query a refresh could repeat) and its answer is not kept:
 * nothing holds it once the page lets go of the mutation or calls `reset()` (`gcTime: 0`).
 */
export function useRevealParty(partyId: string) {
  return useMutation({
    mutationFn: () => unwrap(api.GET('/parties/{party_id}/pii', { params: { path: { party_id: partyId } } })),
    gcTime: 0,
  })
}

/** Create a party. Its variables hold the names and identifiers as typed, so they are not kept either. */
export function useCreateParty() {
  const qc = useQueryClient()
  return useIdempotentMutation<PartyCreateRequest, Replayable<PartyResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(api.POST('/parties', { body, headers: { 'Idempotency-Key': idempotencyKey } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
    gcTime: 0,
  })
}

/** Place a party on legal hold or release it (MLRO only). Setting the same value again changes nothing. */
export function useSetLegalHold(partyId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<LegalHoldRequest, Replayable<PartyResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/parties/{party_id}/legal-hold', {
          params: { path: { party_id: partyId } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
  })
}
