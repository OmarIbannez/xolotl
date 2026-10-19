import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { DeadlineRule, HolidayScope } from '@/api/types'
import { useAuth } from '@/auth/use-auth'

// Holidays and deadlines are reference data: none of these answers holds personal data.
const CALENDAR = 'calendar'

/** The non-business days of `scope` in `year`. */
export function useHolidays(scope: HolidayScope, year: number) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [CALENDAR, 'holidays', user?.tenant_id, scope, year],
    queryFn: () => unwrap(api.GET('/calendar/holidays', { params: { query: { scope, year } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/** When `rule` falls due for `anchor` (an ISO datetime with an offset) on `scope`'s business days. */
export function useDeadline(rule: DeadlineRule, anchor: string, scope: HolidayScope, enabled: boolean) {
  const { user, mustChangePassword } = useAuth()
  const json = JSON.stringify(rule)
  return useQuery({
    queryKey: [CALENDAR, 'due', user?.tenant_id, json, anchor, scope],
    queryFn: () => unwrap(api.GET('/calendar/due', { params: { query: { rule: json, anchor, scope } } })),
    enabled: enabled && user !== undefined && !mustChangePassword,
  })
}
