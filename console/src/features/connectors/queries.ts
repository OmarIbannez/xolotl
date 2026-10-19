import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { useAuth } from '@/auth/use-auth'

const CONNECTORS = 'connectors'

/**
 * The platform's connectors with their mode, routes, circuit state and credential modes.
 * Keyed by tenant like every signed-in query, and held back during a forced password change.
 */
export function useConnectors() {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [CONNECTORS, user?.tenant_id],
    queryFn: () => unwrap(api.GET('/connectors')),
    enabled: user !== undefined && !mustChangePassword,
  })
}

/**
 * Calls, cost and outcomes per connector and UTC day for the tenant, over an inclusive window.
 * The previous window stays on screen while another one loads.
 */
export function useConnectorUsage(window: { from: string; to: string }) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [CONNECTORS, 'usage', user?.tenant_id, window],
    queryFn: () => unwrap(api.GET('/connectors/usage', { params: { query: window } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}
