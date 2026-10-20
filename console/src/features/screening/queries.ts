import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, expectNoContent, unwrap, unwrapReplayable, type Replayable } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type {
  CheckRequest,
  CheckResponse,
  CheckStaffResponse,
  DispositionRequest,
  DispositionResponse,
  HitBand,
  ListKind,
  MatchStatus,
  PartyScreeningResponse,
  PartyScreeningStaffResponse,
} from '@/api/types'
import { useIdempotentMutation } from '@/api/use-idempotent-mutation'
import { useAuth } from '@/auth/use-auth'

// Every screening query sits under this root, so a disposition or a revocation refreshes the
// queue and the details at once. None of them holds plaintext: the queue and the detail are
// masked, and the plaintext comes only from `useRevealMatch`, which keeps nothing.
const SCREENING = 'screening'

/** What the queue is narrowed to. `band` is the tab (alerts before low confidence). */
export interface MatchFilters {
  band: Extract<HitBand, 'ALERT' | 'LOW_CONFIDENCE'>
  status?: MatchStatus
  list_kind?: ListKind
  list_code?: string
  /** An instant: only findings created before it (the "older than" filter). */
  created_before?: string
}

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

/** One page of the screening queue, newest first; `cursor` is the previous page's `next_cursor`. */
export function useMatches(filters: MatchFilters, cursor?: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [SCREENING, tenant, 'matches', cursor ?? null, filters],
    queryFn: () =>
      unwrap(
        api.GET('/screening/matches', {
          params: { query: { limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }), ...filters } },
        }),
      ),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** One finding, masked, with the current user's allowed actions and blocked reasons. */
export function useMatch(matchId: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [SCREENING, tenant, 'match', matchId],
    queryFn: () =>
      unwrap(api.GET('/screening/matches/{match_id}', { params: { path: { match_id: matchId } } })),
    enabled,
  })
}

/**
 * Reveal a finding's personal values (the subject, a private entry, the rationales). Each call is
 * audited as `VIEW_PII`, so it is a mutation the user starts with a button, never a query a
 * refresh could repeat, and its answer is not kept once the card lets go of it (`gcTime: 0`).
 */
export function useRevealMatch(matchId: string) {
  return useMutation({
    mutationFn: () =>
      unwrap(api.GET('/screening/matches/{match_id}/reveal', { params: { path: { match_id: matchId } } })),
    gcTime: 0,
  })
}

/** Decide a finding. The rationale is personal data, so the variables are not kept either. */
export function useDisposition(matchId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<DispositionRequest, Replayable<DispositionResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/screening/matches/{match_id}/dispositions', {
          params: { path: { match_id: matchId } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [SCREENING] })
    },
    gcTime: 0,
  })
}

/** Revoke a whitelist row (MLRO): the entry alerts again on the next screening. */
export function useRevokeWhitelist() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (whitelistId: string) =>
      expectNoContent(
        api.DELETE('/screening/whitelist/{whitelist_id}', { params: { path: { whitelist_id: whitelistId } } }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [SCREENING] })
    },
  })
}

/** The matcher's evaluation report shipped with the build; a 404 means the build ships none. */
export function useEvaluation() {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [SCREENING, tenant, 'evaluation'],
    queryFn: () => unwrap(api.GET('/screening/evaluation')),
    enabled,
    staleTime: Infinity,
  })
}

/** True for the staff view of a party's screening (the console's), which carries the findings. */
export function isStaffPartyScreening(
  view: PartyScreeningResponse | PartyScreeningStaffResponse,
): view is PartyScreeningStaffResponse {
  return 'findings' in view
}

/** True for the staff answer of a check (the console's), which carries the hits. */
export function isStaffCheck(result: CheckResponse | CheckStaffResponse): result is CheckStaffResponse {
  return 'hits' in result
}

/** A party's screening status, last run, lists covered and (staff) open findings. Masked: no plaintext. */
export function usePartyScreening(partyId: string) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [SCREENING, tenant, 'party', partyId],
    queryFn: () =>
      unwrap(api.GET('/screening/parties/{party_id}', { params: { path: { party_id: partyId } } })),
    enabled,
  })
}

/**
 * Screen a party or an inline subject now (`POST /v1/screening/checks`). An ad hoc subject is
 * personal data the user typed, so neither the variables nor the answer outlive the component
 * (`gcTime: 0`): they are never in a query key, the URL or storage. A check changes the
 * subject's status and findings, so the screening queries are refreshed.
 */
export function useCheck() {
  const qc = useQueryClient()
  return useIdempotentMutation<CheckRequest, Replayable<CheckResponse | CheckStaffResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(api.POST('/screening/checks', { body, headers: { 'Idempotency-Key': idempotencyKey } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [SCREENING] })
    },
    gcTime: 0,
  })
}
