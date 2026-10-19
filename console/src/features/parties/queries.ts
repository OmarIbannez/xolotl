import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, unwrapReplayable, type Replayable } from '@/api/client'
import { PAGE_SIZE } from '@/api/queries'
import type {
  AddressInput,
  AddressResponse,
  ContactInput,
  CustomerRelationshipResponse,
  DocumentResponse,
  DocumentType,
  IdentifierInput,
  IdentifierType,
  LegalHoldRequest,
  MaskedContact,
  MaskedIdentifier,
  PartyCreateRequest,
  PartyRelationshipInput,
  PartyRelationshipResponse,
  PartyResponse,
  PartyStaffResponse,
  PartyStatus,
  PartyUpdateRequest,
  RelationshipOpenRequest,
} from '@/api/types'
import { useIdempotentMutation } from '@/api/use-idempotent-mutation'
import { useAuth } from '@/auth/use-auth'

// Every party query sits under this root, so a write can invalidate the list and the details at once.
// None of them holds plaintext personal data: the API masks lists and details for everyone.
const PARTIES = 'parties'

/** What the list is narrowed to. An identifier search sends `id_type` and `value` together. */
export interface PartyFilters {
  status?: PartyStatus
  external_ref?: string
  id_type?: IdentifierType
  value?: string
}

/** The staff shape of a party's detail embeds its sub-collections; the machine shape does not. */
export function isStaffView(p: PartyStaffResponse | PartyResponse): p is PartyStaffResponse {
  return 'contacts' in p
}

/** One page of the tenant's parties, newest first, masked; `cursor` is the previous page's `next_cursor`. */
export function useParties(filters: PartyFilters, cursor?: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [PARTIES, user?.tenant_id, cursor ?? null, filters],
    queryFn: () =>
      unwrap(api.GET('/parties', { params: { query: { limit: PAGE_SIZE, cursor, ...filters } } })),
    enabled: user !== undefined && !mustChangePassword,
    placeholderData: keepPreviousData,
  })
}

/** One party, masked (its plaintext only comes from `useRevealParty`). */
export function useParty(partyId: string) {
  const { user, mustChangePassword } = useAuth()
  return useQuery({
    queryKey: [PARTIES, 'one', user?.tenant_id, partyId],
    queryFn: () => unwrap(api.GET('/parties/{party_id}', { params: { path: { party_id: partyId } } })),
    enabled: user !== undefined && !mustChangePassword,
  })
}

/**
 * Reveal a party's personal data. Each call is audited as `VIEW_PII`, so it is a mutation the
 * user starts with a button (never a query a refresh could repeat) and its answer is not kept:
 * nothing holds it once the page lets go of the mutation or calls `reset()` (`gcTime: 0`).
 */
export function useRevealParty(partyId: string) {
  return useMutation({
    mutationFn: () => unwrap(api.GET('/parties/{party_id}/pii', { params: { path: { party_id: partyId } } })),
    gcTime: 0,
  })
}

/** Create a party. Its variables hold the names and identifiers as typed, so they are not kept either. */
export function useCreateParty() {
  const qc = useQueryClient()
  return useIdempotentMutation<PartyCreateRequest, Replayable<PartyResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(api.POST('/parties', { body, headers: { 'Idempotency-Key': idempotencyKey } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
    gcTime: 0,
  })
}

/** Place a party on legal hold or release it (MLRO only). Setting the same value again changes nothing. */
export function useSetLegalHold(partyId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<LegalHoldRequest, Replayable<PartyResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/parties/{party_id}/legal-hold', {
          params: { path: { party_id: partyId } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
  })
}

/** Change a party's plain attributes. A PATCH: the API needs no `Idempotency-Key` for it. */
export function useUpdateParty(partyId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: PartyUpdateRequest) =>
      unwrap(api.PATCH('/parties/{party_id}', { params: { path: { party_id: partyId } }, body })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
  })
}

// The four sub-resource writes. Their variables hold the values as typed (an identifier, an email,
// a street), so the mutations are not kept once the dialog lets go of them (`gcTime: 0`).

export function useAddIdentifier(partyId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<IdentifierInput, Replayable<MaskedIdentifier>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/parties/{party_id}/identifiers', {
          params: { path: { party_id: partyId } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
    gcTime: 0,
  })
}

export function useAddContact(partyId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<ContactInput, Replayable<MaskedContact>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/parties/{party_id}/contacts', {
          params: { path: { party_id: partyId } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
    gcTime: 0,
  })
}

export function useAddAddress(partyId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<AddressInput, Replayable<AddressResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/parties/{party_id}/addresses', {
          params: { path: { party_id: partyId } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
    gcTime: 0,
  })
}

export function useAddRelationship(partyId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<PartyRelationshipInput, Replayable<PartyRelationshipResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/parties/{party_id}/relationships', {
          params: { path: { party_id: partyId } },
          body,
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
  })
}

/**
 * What an upload sends. `name`, `size` and `lastModified` are there so the idempotency runner,
 * which compares variables as JSON (a `File` serialises to `{}`), tells two files apart.
 */
export interface DocumentUpload {
  file: File
  docType: DocumentType
  name: string
  size: number
  lastModified: number
}

/** Upload a document: the file is the raw request body, typed by its own content type. */
export function useUploadDocument(partyId: string) {
  const qc = useQueryClient()
  return useIdempotentMutation<DocumentUpload, Replayable<DocumentResponse>>({
    mutationFn: ({ file, docType }, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/parties/{party_id}/documents', {
          params: { path: { party_id: partyId }, query: { doc_type: docType } },
          // The OpenAPI type of a binary body is `string`; the browser sends the File as is.
          body: file as unknown as string,
          bodySerializer: (body) => body as unknown as BodyInit,
          headers: { 'Content-Type': file.type, 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
  })
}

const EXTENSION: Record<string, string> = { 'application/pdf': 'pdf', 'image/jpeg': 'jpg', 'image/png': 'png' }

/** The name a downloaded document is saved under, as the API's `Content-Disposition` names it. */
export function documentFilename(document: Pick<DocumentResponse, 'id' | 'content_type'>): string {
  const extension = EXTENSION[document.content_type]
  return extension === undefined ? document.id : `${document.id}.${extension}`
}

/**
 * Download a document and hand it to the browser to save. Each download is audited as
 * `VIEW_PII`, so it runs on a click only; the file is held in memory just long enough to save it.
 */
export async function downloadDocument(partyId: string, documentId: string, filename: string): Promise<void> {
  const blob = await unwrap(
    api.GET('/parties/{party_id}/documents/{document_id}/content', {
      params: { path: { party_id: partyId, document_id: documentId } },
      parseAs: 'blob',
    }),
  )
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
  } finally {
    URL.revokeObjectURL(url)
  }
}

/** Open a customer relationship for a party. */
export function useOpenRelationship() {
  const qc = useQueryClient()
  return useIdempotentMutation<RelationshipOpenRequest, Replayable<CustomerRelationshipResponse>>({
    mutationFn: (body, idempotencyKey) =>
      unwrapReplayable(api.POST('/relationships', { body, headers: { 'Idempotency-Key': idempotencyKey } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
  })
}

/** Close a customer relationship now; the API starts the party's retention clock from it. */
export function useCloseRelationship() {
  const qc = useQueryClient()
  return useIdempotentMutation<{ relationshipId: string }, Replayable<CustomerRelationshipResponse>>({
    mutationFn: ({ relationshipId }, idempotencyKey) =>
      unwrapReplayable(
        api.POST('/relationships/{relationship_id}/close', {
          params: { path: { relationship_id: relationshipId } },
          body: {},
          headers: { 'Idempotency-Key': idempotencyKey },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PARTIES] })
    },
  })
}
