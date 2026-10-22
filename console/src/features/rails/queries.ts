import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { FailMode, Rail } from '@/api/types'
import { useAuth } from '@/auth/use-auth'

const RAILS = 'rails'

/** Every rail's fail mode: its own setting, or the deployment's default (`explicit: false`). */
export function useRails() {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [RAILS, user?.tenant_id],
    queryFn: () => unwrap(api.GET('/monitoring/rails')),
    enabled: user !== undefined && !mustChangePassword,
  })
}

/**
 * Set a rail's fail mode (ADMIN; audited as `monitoring.rail_setting_changed`). A PUT: the platform
 * keys POSTs only, and sending the same mode again stores it again, so no `Idempotency-Key`.
 */
export function useSetRailMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ rail, failMode }: { rail: Rail; failMode: FailMode }) =>
      unwrap(api.PUT('/monitoring/rails/{rail}', { params: { path: { rail } }, body: { fail_mode: failMode } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [RAILS] })
    },
  })
}
