import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/auth/use-auth'
import { api, unwrap, unwrapReplayable, type Replayable } from './client'
import type {
  ApiKeyCreatedResponse,
  ApiKeyCreateRequest,
  ApiKeyResponse,
  StaffCreatedResponse,
  StaffCreateRequest,
  StaffResponse,
} from './types'
import { useIdempotentMutation } from './use-idempotent-mutation'

/**
 * The signed-in user's tenant. Waits for the password change: until then the API
 * answers every route except /auth/* with 403 PASSWORD_CHANGE_REQUIRED.
 */
export function useTenant() {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: ['tenant', user?.tenant_id],
    queryFn: () => unwrap(api.GET('/tenants/me')),
    enabled: user !== undefined && !mustChangePassword,
    staleTime: 5 * 60_000,
  })
}

/** Rows per page of the admin tables. The API accepts 1 to 200 (50 by default). */
export const PAGE_SIZE = 20

// List queries are keyed by tenant and cursor under one root each, so a mutation can
// invalidate every page of its list at once.
const API_KEYS = 'api-keys'
const STAFF = 'staff'

/** One page of the tenant's API keys, newest first; `cursor` is the previous page's `next_cursor`. */
export function useApiKeys(cursor?: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [API_KEYS, user?.tenant_id, cursor ?? null],
    queryFn: () => unwrap(api.GET('/api-keys', { params: { query: { limit: PAGE_SIZE, cursor } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData, // keep the old rows on screen while the next page loads
  })
}

/**
 * Create an API key. The result holds the full `key`, which the API never shows again, so the
 * caller keeps it only as long as it is on screen and the mutation cache does not keep it at all.
 */
export function useCreateApiKey() {
  const qc = useQueryClient()
  return useIdempotentMutation<ApiKeyCreateRequest, Replayable<ApiKeyCreatedResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/api-keys', { body, headers: { 'Idempotency-Key': idempotencyKey } }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [API_KEYS] })
    },
    gcTime: 0,
  })
}

/** Revoke an API key by id. Revoking a revoked key is not an error. */
export function useRevokeApiKey() {
  const qc = useQueryClient()
  return useIdempotentMutation<string, Replayable<ApiKeyResponse>>({
    mutationFn: (apiKeyId, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/api-keys/{api_key_id}/revoke', {
          params: { path: { api_key_id: apiKeyId } },
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [API_KEYS] })
    },
    gcTime: 0,
  })
}

/** One page of the tenant's staff, newest first; `cursor` is the previous page's `next_cursor`. */
export function useStaff(cursor?: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [STAFF, user?.tenant_id, cursor ?? null],
    queryFn: () => unwrap(api.GET('/staff', { params: { query: { limit: PAGE_SIZE, cursor } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/**
 * Create a staff account. The result holds the `initial_password`, which the API never shows
 * again: same care as for an API key's secret.
 */
export function useCreateStaff() {
  const qc = useQueryClient()
  return useIdempotentMutation<StaffCreateRequest, Replayable<StaffCreatedResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(api.POST('/staff', { body, headers: { 'Idempotency-Key': idempotencyKey } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STAFF] })
    },
    gcTime: 0,
  })
}

/** Disable a staff account by id. Disabling your own account is a 409 `CONFLICT`. */
export function useDisableStaff() {
  const qc = useQueryClient()
  return useIdempotentMutation<string, Replayable<StaffResponse>>({
    mutationFn: (staffUserId, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/staff/{staff_user_id}/disable', {
          params: { path: { staff_user_id: staffUserId } },
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STAFF] })
    },
    gcTime: 0,
  })
}
