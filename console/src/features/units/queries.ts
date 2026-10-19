import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { ConversionRule } from '@/api/types'
import { useAuth } from '@/auth/use-auth'

// Unit values are public reference data (UMA, UDI, FIX): none of these answers holds
// personal data.
const UNITS = 'units'

/** Every unit the platform knows, with its latest value date, fetch time and source. */
export function useUnits() {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [UNITS, 'summary', user?.tenant_id],
    queryFn: () => unwrap(api.GET('/units')),
    enabled: user !== undefined && !mustChangePassword,
  })
}

/** The stored values of `code` valid between `from` and `to` (YYYY-MM-DD), corrections included. */
export function useUnitValues(code: string, from: string, to: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [UNITS, 'values', user?.tenant_id, code, from, to],
    queryFn: () => unwrap(api.GET('/units/{code}/values', { params: { path: { code }, query: { from, to } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/**
 * The value of `code` that `rule` resolves for `on`. A 503 UNIT_VALUE_MISSING means the
 * value has not been published or fetched; nothing fills it in.
 */
export function useUnitLookup(code: string, on: string, rule: ConversionRule, enabled: boolean) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [UNITS, 'lookup', user?.tenant_id, code, on, rule],
    queryFn: () => unwrap(api.GET('/units/{code}', { params: { path: { code }, query: { on, rule } } })),
    enabled: enabled && user !== undefined && !mustChangePassword,
  })
}
