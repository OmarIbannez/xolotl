import type { AuditEventResponse, AuditPage } from '@/api/types'

// The schemas are aliased once, in `@/api/types`, from the generated OpenAPI types; these are
// the audit feature's names for them.
export type { ChainVerification } from '@/api/types'

/** One link of the tenant's audit chain, as `GET /v1/audit` lists it (newest first). */
export type AuditEvent = AuditEventResponse

/** The `detail` of an event: a JSON object, or null when the event carries none. */
export type AuditDetail = AuditEvent['detail']

export type AuditEventPage = AuditPage
