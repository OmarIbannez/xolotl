/** The catalogue keys for the reasons `GET /v1/audit/verify` gives when a chain is broken. */
type ReasonKey =
  | 'audit.verify.reason.sequence'
  | 'audit.verify.reason.prevHash'
  | 'audit.verify.reason.content'
  | 'audit.verify.reason.unknown'

/** The API's reasons are fixed English phrases (spec section 8.3). */
const KNOWN_REASONS: ReadonlyMap<string, ReasonKey> = new Map([
  ['sequence gap or duplicate', 'audit.verify.reason.sequence'],
  ['prev_hash does not match the previous entry', 'audit.verify.reason.prevHash'],
  ['content does not match its hash', 'audit.verify.reason.content'],
])

/** Which message explains `reason`; a reason this console does not know gets a generic one. */
export function reasonKey(reason: string | null | undefined): ReasonKey {
  const known = reason == null ? undefined : KNOWN_REASONS.get(reason)
  return known ?? 'audit.verify.reason.unknown'
}

/** True when the console has a translation for `reason` (else it shows the API's words too). */
export function isKnownReason(reason: string | null | undefined): boolean {
  return reason != null && KNOWN_REASONS.has(reason)
}
