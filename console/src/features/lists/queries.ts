import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import { useAuth } from '@/auth/use-auth'

const LISTS = 'lists'

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

const withCursor = (cursor: string | undefined) => ({
  limit: PAGE_SIZE,
  ...(cursor === undefined ? {} : { cursor }),
})

/** The tenant's subscribed lists with their active version and freshness. */
export function useListOverview() {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [LISTS, tenant, 'overview'],
    queryFn: () => unwrap(api.GET('/lists')),
    enabled,
  })
}

/** One page of a list's versions, newest first. */
export function useListVersions(code: string, cursor: string | undefined) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [LISTS, tenant, code, 'versions', cursor],
    queryFn: () =>
      unwrap(api.GET('/lists/{code}/versions', { params: { path: { code }, query: withCursor(cursor) } })),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** One page of a version's delta, by entry key. Waits until a version is selected. */
export function useListDelta(code: string, versionId: string | undefined, cursor: string | undefined) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [LISTS, tenant, code, 'delta', versionId, cursor],
    queryFn: () =>
      unwrap(
        api.GET('/lists/{code}/versions/{version_id}/delta', {
          params: { path: { code, version_id: versionId ?? '' }, query: withCursor(cursor) },
        }),
      ),
    enabled: enabled && versionId !== undefined,
    placeholderData: keepPreviousData,
  })
}

/** One list entry (public list data). Waits until an entry is chosen. */
export function useListEntry(code: string, entryId: string | undefined) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [LISTS, tenant, code, 'entry', entryId],
    queryFn: () =>
      unwrap(api.GET('/lists/{code}/entries/{entry_id}', { params: { path: { code, entry_id: entryId ?? '' } } })),
    enabled: enabled && entryId !== undefined,
  })
}

/** Turn a recommended list on or off (ADMIN). A PATCH: the API needs no `Idempotency-Key`. */
export function useSetSubscription() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ code, enabled }: { code: string; enabled: boolean }) =>
      unwrap(api.PATCH('/lists/subscriptions/{code}', { params: { path: { code } }, body: { enabled } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [LISTS] })
    },
  })
}
