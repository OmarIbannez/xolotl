import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { useAuth } from '@/auth/use-auth'

/** The API's default page size; stated so the page does not depend on it staying so. */
export const AUDIT_PAGE_SIZE = 50

export interface AuditListParams {
  /** Exact action to show; '' for every action. */
  action: string
  /** Where the page starts: the cursor the previous page handed out. Undefined for the newest. */
  cursor: string | undefined
}

/**
 * One page of the tenant's audit log, newest first. Waits for the forced password change
 * like the other signed-in queries, and is keyed by tenant so one session's log can never
 * be served to another. The previous page stays on screen while the next one loads.
 */
export function useAuditEvents({ action, cursor }: AuditListParams) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: ['audit', 'events', user?.tenant_id, { action, cursor }],
    queryFn: () =>
      unwrap(
        api.GET('/audit', {
          params: {
            query: {
              limit: AUDIT_PAGE_SIZE,
              // An empty `action` would ask for events whose action is the empty string.
              ...(action === '' ? {} : { action }),
              ...(cursor === undefined ? {} : { cursor }),
            },
          },
        }),
      ),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/**
 * Walk the tenant's whole chain and report whether it is intact. A read, but one that is
 * asked for on purpose and whose answer is only good for the moment it was given, so it is
 * a mutation: nothing is cached, refetched or shown stale, and leaving the page forgets it.
 */
export function useVerifyAuditChain() {
  return useMutation({
    mutationFn: () => unwrap(api.GET('/audit/verify')),
    gcTime: 0,
  })
}
