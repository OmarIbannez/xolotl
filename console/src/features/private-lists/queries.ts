import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, unwrapReplayable, type Replayable } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type { PrivateListCreate, PrivateListResponse, PrivateListVersionResponse } from '@/api/types'
import { useIdempotentMutation } from '@/api/use-idempotent-mutation'
import { useAuth } from '@/auth/use-auth'

// Every private-list query sits under this root, so a create or an upload refreshes them all.
// None of them holds an entry: entries come only from `useRevealEntries`, which keeps nothing.
const PRIVATE_LISTS = 'private-lists'

/**
 * How many lists one request brings (the API's largest page). A tenant has its LPB and a few
 * internal lists, so the overview and the LPB banner read them in one page.
 */
const ALL_LISTS = 200

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

/** The tenant's private lists, each with its active version (no entries). */
export function usePrivateLists() {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [PRIVATE_LISTS, tenant, 'all'],
    queryFn: () => unwrap(api.GET('/private-lists', { params: { query: { limit: ALL_LISTS } } })),
    enabled,
  })
}

/** One private list, found among the tenant's lists (the API has no single-list read). */
export function usePrivateList(listId: string): { list: PrivateListResponse | undefined; isPending: boolean } {
  const lists = usePrivateLists()
  return { list: lists.data?.items.find((item) => item.id === listId), isPending: lists.isPending }
}

/** One page of a list's versions, newest first. */
export function usePrivateListVersions(listId: string, cursor: string | undefined) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [PRIVATE_LISTS, tenant, listId, 'versions', cursor],
    queryFn: () =>
      unwrap(
        api.GET('/private-lists/{list_id}/versions', {
          params: { path: { list_id: listId }, query: { limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
        }),
      ),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** Create the LPB or an internal list. */
export function useCreatePrivateList() {
  const qc = useQueryClient()
  return useIdempotentMutation<PrivateListCreate, Replayable<PrivateListResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(api.POST('/private-lists', { body, headers: { 'Idempotency-Key': idempotencyKey } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PRIVATE_LISTS] })
    },
  })
}

/**
 * What an upload sends. `name`, `size` and `lastModified` are there so the idempotency runner,
 * which compares variables as JSON (a `File` serialises to `{}`), tells two files apart.
 */
export interface PrivateListUpload {
  file: File
  contentType: string
  name: string
  size: number
  lastModified: number
}

/** Upload a new version: the file is the raw request body, typed CSV or XLSX. */
export function useUploadPrivateList(listId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<PrivateListUpload, Replayable<PrivateListVersionResponse>>({
    mutationFn: ({ file, contentType }, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/private-lists/{list_id}/versions', {
          params: { path: { list_id: listId } },
          // The OpenAPI type of a binary body is `string`; the browser sends the File as is.
          body: file as unknown as string,
          bodySerializer: (body) => body as unknown as BodyInit,
          headers: { 'Content-Type': contentType, 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PRIVATE_LISTS] })
    },
    // The variables hold the File, which carries personal data: drop them once the card lets go.
    gcTime: 0,
  })
}

/**
 * Reveal one page of the active version's entries (`cursor` undefined for the first). Each call
 * is audited as `VIEW_PII` and `private_list.entries_viewed`, so it is a mutation the user starts
 * with a button, and its answer is not kept once the card lets go of it (`gcTime: 0`).
 */
export function useRevealEntries(listId: string) {
  return useMutation({
    mutationFn: (cursor: string | undefined) =>
      unwrap(
        api.GET('/private-lists/{list_id}/entries', {
          params: { path: { list_id: listId }, query: { limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
        }),
      ),
    gcTime: 0,
  })
}
