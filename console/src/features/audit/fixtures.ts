import type { AuditEvent, AuditEventPage, ChainVerification } from './types'

/** An audit event as the API sends it, for tests. Override what a test cares about. */
export function makeEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: '0191d3a2-6b1c-7000-8000-0000000000e1',
    seq: 7,
    at: '2026-10-18T16:24:05.123456Z',
    actor_type: 'STAFF',
    actor_id: '0191d3a2-6b1c-7000-8000-000000000001',
    action: 'staff.created',
    entity_type: 'staff_user',
    entity_id: '0191d3a2-6b1c-7000-8000-000000000002',
    detail: { roles: ['ANALYST'] },
    prev_hash: 'a1'.repeat(32),
    hash: 'b2'.repeat(32),
    ...overrides,
  }
}

/** `n` events with distinct ids and sequence numbers, newest (highest seq) first. */
export function makeEvents(n: number, newestSeq = n): AuditEvent[] {
  return Array.from({ length: n }, (_, i) =>
    makeEvent({
      id: `0191d3a2-6b1c-7000-8000-${String(1000 + newestSeq - i).padStart(12, '0')}`,
      seq: newestSeq - i,
    }),
  )
}

export function pageOf(items: AuditEvent[], nextCursor: string | null = null): AuditEventPage {
  return { items, next_cursor: nextCursor }
}

export function verification(overrides: Partial<ChainVerification> = {}): ChainVerification {
  return {
    tenant_id: '0191d3a2-6b1c-7000-8000-0000000000aa',
    valid: true,
    entries_checked: 3,
    first_broken_seq: null,
    reason: null,
    ...overrides,
  }
}
