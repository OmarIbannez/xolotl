import type { PrivateListEntryResponse, PrivateListResponse, PrivateListVersionResponse } from '@/api/types'

export function privateVersion(overrides: Partial<PrivateListVersionResponse> = {}): PrivateListVersionResponse {
  return {
    id: 'pv2',
    seq: 2,
    status: 'ACTIVE',
    uploaded_by: '0191d3a2-6b1c-7000-8000-000000000001',
    uploaded_at: '2026-10-19T11:45:00Z',
    content_type: 'text/csv',
    file_size: 2048,
    entry_count: 41,
    added: 3,
    changed: 1,
    removed: 2,
    ...overrides,
  }
}

/** A private list as `GET /v1/private-lists` answers it: the LPB with an active version. */
export function privateList(overrides: Partial<PrivateListResponse> = {}): PrivateListResponse {
  return {
    id: 'l1',
    list_code: 'MX_LPB',
    list_kind: 'BLOCKED',
    title: 'Lista de Personas Bloqueadas',
    created_at: '2026-10-01T09:00:00Z',
    active_version: privateVersion(),
    ...overrides,
  }
}

export function privateEntry(overrides: Partial<PrivateListEntryResponse> = {}): PrivateListEntryResponse {
  return {
    id: 'pe1',
    source_entry_key: 'clave LPB-001',
    entity_type: 'PERSON',
    entry: {
      list_code: 'MX_LPB',
      entry_key: 'LPB-001',
      entity_type: 'PERSON',
      primary_name: { full: 'Persona Ficticia Uno', quality: 'PRIMARY', given: null, surname: null, script: null },
      aliases: [],
      birth_dates: [],
      birth_places: [],
      nationalities: ['MX'],
      gender: null,
      identifiers: [{ id_type: 'RFC', value: 'XAXX010101000', normalized: 'XAXX010101000', country: 'MX', label: null }],
      addresses: [],
      programs: [],
      listed_on: null,
      delisted_on: null,
      status: null,
      screenable: true,
      source_ref: 'clave LPB-001',
    },
    ...overrides,
  }
}
