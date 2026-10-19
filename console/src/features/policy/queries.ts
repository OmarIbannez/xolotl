import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import { useAuth } from '@/auth/use-auth'

// None of these answers holds personal data: packs, assignments and overrides name codes,
// dates and staff ids only.
const POLICY = 'policy'

/**
 * The tenant's effective policy on `on` (YYYY-MM-DD) for `product` (every product when
 * undefined), with its snapshot hash and the MXN conversions. A 404 POLICY_NOT_ASSIGNED
 * means no pack is in force on that date.
 */
export function useEffectivePolicy(on: string, product: string | undefined) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [POLICY, 'effective', user?.tenant_id, on, product ?? null],
    queryFn: () =>
      unwrap(api.GET('/policy/effective', { params: { query: product ? { on, product } : { on } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/** One page of the pack versions the platform has loaded. */
export function usePackVersions(cursor?: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [POLICY, 'packs', user?.tenant_id, cursor ?? null],
    queryFn: () => unwrap(api.GET('/policy/packs', { params: { query: { limit: PAGE_SIZE, cursor } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/** One page of the tenant's pack assignments. */
export function useAssignments(cursor?: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [POLICY, 'assignments', user?.tenant_id, cursor ?? null],
    queryFn: () =>
      unwrap(api.GET('/policy/assignments', { params: { query: { limit: PAGE_SIZE, cursor } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/** One page of the tenant's override versions. */
export function useOverrides(cursor?: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [POLICY, 'overrides', user?.tenant_id, cursor ?? null],
    queryFn: () =>
      unwrap(api.GET('/policy/overrides', { params: { query: { limit: PAGE_SIZE, cursor } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}
