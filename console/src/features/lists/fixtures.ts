import type { DeltaRowResponse, ListEntryResponse, ListOverview, ListVersionResponse } from '@/api/types'

/** A subscribed list with an active version, fresh, as `GET /v1/lists` answers it. */
export function overview(overrides: Partial<ListOverview> = {}): ListOverview {
  return {
    code: 'UN_CONSOLIDATED',
    list_kind: 'SANCTION',
    title: 'UN Security Council Consolidated List',
    jurisdiction: 'UN',
    requirement: 'MANDATORY',
    enabled: true,
    active_seq: 2,
    published_at: '2026-10-18T09:00:00Z',
    fetched_at: '2026-10-19T11:45:00Z',
    entry_count: 1003,
    stale: false,
    failing: false,
    last_error: null,
    last_success_at: '2026-10-19T11:45:00Z',
    ...overrides,
  }
}

export function version(overrides: Partial<ListVersionResponse> = {}): ListVersionResponse {
  return {
    id: 'v2',
    seq: 2,
    status: 'ACTIVE',
    published_at: '2026-10-18T09:00:00Z',
    source_version: null,
    parser_version: 'un-1',
    fetched_at: '2026-10-19T11:45:00Z',
    loaded_by: 'poll',
    file_sha256: 'ab'.repeat(32),
    file_size: 2048,
    entry_count: 1003,
    added: 1,
    changed: 1,
    removed: 1,
    ...overrides,
  }
}

export function deltaRow(overrides: Partial<DeltaRowResponse> = {}): DeltaRowResponse {
  return {
    source_entry_key: 'QDi.001',
    change: 'ADDED',
    prev_entry_id: null,
    new_entry_id: 'e-new-1',
    prev_name: null,
    new_name: 'Nuevo Nombre',
    ...overrides,
  }
}

export function listEntry(overrides: Partial<ListEntryResponse> = {}): ListEntryResponse {
  return {
    id: 'e-new-2',
    list_code: 'UN_CONSOLIDATED',
    source_entry_key: 'QDi.002',
    from_seq: 2,
    to_seq: null,
    entry: {
      list_code: 'UN_CONSOLIDATED',
      entry_key: 'QDi.002',
      entity_type: 'PERSON',
      primary_name: { full: 'Nombre Nuevo', quality: 'PRIMARY', given: null, surname: null, script: null },
      aliases: [{ full: 'Alias Uno', quality: 'WEAK', given: null, surname: null, script: null }],
      birth_dates: [{ year: 1970, month: 3, day: null }],
      birth_places: [],
      nationalities: ['MX', 'AF'],
      gender: null,
      identifiers: [{ id_type: 'PASSPORT', value: 'P-123', normalized: 'P123', country: 'AF', label: null }],
      addresses: [],
      programs: ['QDi'],
      listed_on: '2001-01-25',
      delisted_on: null,
      status: null,
      screenable: true,
      source_ref: '',
    },
    ...overrides,
  }
}
