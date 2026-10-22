import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, unwrapReplayable, type Replayable } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type {
  BackfillAccepted,
  BackfillRequest,
  FileStatus,
  FileStatusResponse,
  TransactionClientView,
  TransactionQuery,
  TransactionStaffView,
} from '@/api/types'
import { useIdempotentMutation } from '@/api/use-idempotent-mutation'
import { useAuth } from '@/auth/use-auth'

// Every transactions query sits under this root, so an upload or a backfill refreshes them all.
// None of them holds plaintext personal data: the staff view masks the counterparty.
const TRANSACTIONS = 'transactions'

/** How often an uploaded file's status is read again while the worker has not finished it. */
export const FILE_POLL_MS = 5_000

const UNFINISHED: readonly FileStatus[] = ['UPLOADED', 'PROCESSING']

/** The list queries an upload's rows can appear in. */
const TRANSACTION_LISTS = ['list', 'parked', 'backfill'] as const

/** Signed-in queries wait for the profile and for a forced password change, and key by tenant. */
function useSignedIn() {
  const { user, mustChangePassword } = useAuth()
  return { tenant: user?.tenant_id, enabled: user !== undefined && !mustChangePassword }
}

/** Drop the filters left unset, so the request and the cache key carry only real ones. */
function compact(filters: TransactionQuery): TransactionQuery {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined && value !== null && value !== ''))
}

/** The console is always given the staff view; an API client's shape lacks `party_id`. */
export function isStaffTransaction(row: TransactionStaffView | TransactionClientView): row is TransactionStaffView {
  return 'party_id' in row
}

/** The staff rows of a page (all of them, for the console). */
export function staffRows(items: readonly (TransactionStaffView | TransactionClientView)[]): TransactionStaffView[] {
  return items.filter(isStaffTransaction)
}

/**
 * One page of the tenant's transactions, newest first. The OpenAPI document describes the filters
 * as one object parameter, but the API reads them as plain query parameters (`party_id=…`), so the
 * object is serialised in the form style, each filter a parameter of its own.
 */
export function useTransactions(filters: TransactionQuery, cursor?: string) {
  const { tenant, enabled } = useSignedIn()
  const query = compact(filters)
  return useQuery({
    queryKey: [TRANSACTIONS, tenant, 'list', query, cursor ?? null],
    queryFn: () =>
      unwrap(
        api.GET('/transactions', {
          params: { query: { query, limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
          querySerializer: { object: { style: 'form', explode: true } },
        }),
      ),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** One page of the parked transactions (`parked` says why each waits). */
export function useParkedTransactions(cursor: string | undefined) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [TRANSACTIONS, tenant, 'parked', cursor ?? null],
    queryFn: () =>
      unwrap(
        api.GET('/transactions/parked', {
          params: { query: { limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
        }),
      ),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** One page of the transactions that arrived too late and wait for a backfill. */
export function useBackfillTransactions(cursor: string | undefined) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [TRANSACTIONS, tenant, 'backfill', cursor ?? null],
    queryFn: () =>
      unwrap(
        api.GET('/transactions/backfill', {
          params: { query: { limit: PAGE_SIZE, ...(cursor === undefined ? {} : { cursor }) } },
        }),
      ),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/**
 * An uploaded file's status, read again every `pollMs` while the worker has not finished it
 * (`UPLOADED` or `PROCESSING`) and no more once it is `COMPLETED` or `FAILED`. `initial` is the
 * upload's own answer, shown until the first read. When a read finds the file newly finished, the
 * transaction lists are read again, since its rows are in them now.
 */
export function useTransactionFile(fileId: string, initial: FileStatusResponse | undefined, pollMs = FILE_POLL_MS) {
  const { tenant, enabled } = useSignedIn()
  const qc = useQueryClient()
  const queryKey = [TRANSACTIONS, tenant, 'file', fileId]
  return useQuery({
    queryKey,
    queryFn: async () => {
      const before = qc.getQueryData<FileStatusResponse>(queryKey)?.status
      const file = await unwrap(api.GET('/transactions/files/{file_id}', { params: { path: { file_id: fileId } } }))
      // The worker has just finished the file: its rows are in the lists now, so read them again.
      if ((before === undefined || UNFINISHED.includes(before)) && !UNFINISHED.includes(file.status)) {
        for (const list of TRANSACTION_LISTS) void qc.invalidateQueries({ queryKey: [TRANSACTIONS, tenant, list] })
      }
      return file
    },
    enabled,
    initialData: initial,
    // The upload's answer is fresh, but a known file's (200) may be old: read it again soon either way.
    staleTime: 0,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === undefined || UNFINISHED.includes(status) ? pollMs : false
    },
  })
}

/** One batch by id; nothing is asked until there is an id. */
export function useTransactionBatch(batchId: string | undefined) {
  const { tenant, enabled } = useSignedIn()
  return useQuery({
    queryKey: [TRANSACTIONS, tenant, 'batch', batchId],
    queryFn: () =>
      unwrap(api.GET('/transactions/batches/{batch_id}', { params: { path: { batch_id: batchId ?? '' } } })),
    enabled: enabled && batchId !== undefined,
  })
}

/**
 * What an upload sends. `name`, `size` and `lastModified` are there so the idempotency runner,
 * which compares variables as JSON (a `File` serialises to `{}`), tells two files apart.
 */
export interface TransactionFileUpload {
  file: File
  contentType: string
  name: string
  size: number
  lastModified: number
}

/** An upload's answer: `created` is false when the API already had these bytes (200, not 202). */
export interface UploadedFile {
  file: FileStatusResponse
  created: boolean
}

/** Upload a transactions file: the file is the raw request body, typed CSV, JSON Lines or XLSX. */
export function useUploadTransactionFile() {
  const qc = useQueryClient()
  return useIdempotentMutation<TransactionFileUpload, UploadedFile>({
    mutationFn: async ({ file, contentType }, idempotencyKey) => {
      const call = api.POST('/transactions/files', {
        // The OpenAPI type of a binary body is `string`; the browser sends the File as is.
        body: file as unknown as string,
        bodySerializer: (body) => body as unknown as BodyInit,
        headers: { 'Content-Type': contentType, 'Idempotency-Key': idempotencyKey },
      })
      const { response } = await call
      const { data } = await unwrapReplayable(call)
      return { file: data, created: response.status === 202 }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [TRANSACTIONS] })
    },
    // The variables hold the File, which carries personal data: drop them once the dialog lets go.
    gcTime: 0,
  })
}

/**
 * Download a file's row-error report (`row,column,code`, never a cell value) and hand it to the
 * browser to save. It runs on a click only.
 */
export async function downloadFileErrors(fileId: string): Promise<void> {
  const blob = await unwrap(
    api.GET('/transactions/files/{file_id}/errors', { params: { path: { file_id: fileId } }, parseAs: 'blob' }),
  )
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = `${fileId}.errors.csv`
    link.click()
  } finally {
    URL.revokeObjectURL(url)
  }
}

/** Request a backfill of the late transactions in a range (MLRO and ADMIN). */
export function useRequestBackfill() {
  const qc = useQueryClient()
  return useIdempotentMutation<BackfillRequest, Replayable<BackfillAccepted>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(api.POST('/monitoring/backfill', { body, headers: { 'Idempotency-Key': idempotencyKey } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [TRANSACTIONS] })
    },
  })
}
