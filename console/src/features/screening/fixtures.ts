import type {
  CheckStaffResponse,
  HitView,
  MaskedSubject,
  MatchDetail,
  MatchReveal,
  MatchSummary,
  PartyScreeningStaffResponse,
} from '@/api/types'

/** A finding as the queue lists it: an alert on OFAC SDN for a customer. */
export function matchSummary(overrides: Partial<MatchSummary> = {}): MatchSummary {
  return {
    id: 'm1',
    subject_id: 's1',
    party_id: 'p1',
    subject: maskedSubject(),
    list_code: 'OFAC_SDN',
    list_kind: 'SANCTION',
    band: 'ALERT',
    score: '0.9312',
    status: 'OPEN',
    suggested_disposition: null,
    created_at: '2026-10-19T10:00:00Z',
    updated_at: '2026-10-19T10:00:00Z',
    ...overrides,
  }
}

/** The subject of `matchSummary()` as every staff view shows it: initials and masked identifiers only. */
export function maskedSubject(overrides: Partial<MaskedSubject> = {}): MaskedSubject {
  return {
    kind: 'PARTY',
    party_id: 'p1',
    entity_type: 'PERSON',
    initials: 'J. P. L.',
    identifiers: ['RFC ********P8'],
    ...overrides,
  }
}

/** The stored explanation of a hit (`screening_hit.features`): normalized tokens only. */
export function features(overrides: Record<string, unknown> = {}): HitView['features'] {
  return {
    v: 1,
    query_name: 'juan perez lopez',
    entry_name: 'juan peres lopes',
    entry_name_quality: 'PRIMARY',
    name_score: 0.9312,
    alignment: [
      { query: 'juan', entry: 'juan', kind: 'EXACT', similarity: 1, weight: 0.21, role: 'GIVEN' },
      { query: 'perez', entry: 'peres', kind: 'PHONETIC', similarity: 0.92, weight: 0.4, role: 'SURNAME' },
    ],
    unmatched_query: ['lopez'],
    unmatched_entry: [],
    qualifiers: [
      { attribute: 'BIRTH_DATE', outcome: 'MATCH', adjustment: 0.05, detail: 'year 1976' },
      { attribute: 'NATIONALITY', outcome: 'CONFLICT', adjustment: -0.1, detail: 'MX vs VE' },
    ],
    rules: [{ code: 'ID_MATCH_FLOOR', detail: 'RFC matches' }],
    list_kind: 'SANCTION',
    thresholds: { alert: '0.85', review: '0.70' },
    matcher_version: 'm-2026.10',
    config_hash: 'abc123',
    entry_ref: { list_code: 'OFAC_SDN', entry_key: 'SDN-1', list_entry_id: 'e1', list_version_seq: 7 },
    ...overrides,
  } as HitView['features']
}

export function hit(overrides: Partial<HitView> = {}): HitView {
  return {
    id: 'h2',
    list_code: 'OFAC_SDN',
    list_kind: 'SANCTION',
    band: 'ALERT',
    score: '0.9312',
    auto_reason: null,
    match_id: 'm1',
    features: features(),
    ...overrides,
  }
}

/** The staff detail of `matchSummary()`, open, which the current user may clear or escalate. */
export function matchDetail(overrides: Partial<MatchDetail> = {}): MatchDetail {
  return {
    ...matchSummary(),
    entry: {
      list_code: 'OFAC_SDN',
      entry_key: 'SDN-1',
      entity_type: 'PERSON',
      primary_name: { full: 'Juan Peres Lopes', quality: 'PRIMARY', given: null, surname: null, script: null },
      aliases: [{ full: 'El Juanito', quality: 'WEAK', given: null, surname: null, script: null }],
      birth_dates: [{ year: 1976, month: 5, day: null }],
      birth_places: [],
      nationalities: ['VE'],
      gender: null,
      identifiers: [{ id_type: 'PASSPORT', value: 'V12345678', normalized: 'V12345678', country: 'VE', label: null }],
      addresses: [],
      programs: ['SDNTK'],
      listed_on: '2019-01-08',
    },
    origin_hit: hit({ id: 'h1', score: '0.9001' }),
    latest_hit: hit(),
    history: [
      { hit_id: 'h1', run_id: 'r1', created_at: '2026-10-19T10:00:00Z', score: '0.9001', band: 'ALERT' },
      { hit_id: 'h2', run_id: 'r2', created_at: '2026-10-20T08:00:00Z', score: '0.9312', band: 'ALERT' },
    ],
    dispositions: [],
    whitelist_id: null,
    allowed_actions: ['FALSE_POSITIVE', 'POSSIBLE_TRUE_MATCH'],
    blocked_reasons: { TRUE_MATCH: ['STATE', 'ROLE_REQUIRED'], DELISTED: ['NOT_DELISTED'] },
    ...overrides,
  }
}

/** What the reveal answers: plaintext a test must never find on the page before the reveal. */
export function matchReveal(overrides: Partial<MatchReveal> = {}): MatchReveal {
  return {
    subject_names: ['Juan Pérez López'],
    birth_date: '1976-05-10',
    identifiers: [{ id_type: 'RFC', value: 'PELJ760510AB8' }],
    private_entry: null,
    rationales: {},
    ...overrides,
  }
}

/** A screened party with one open alert, as staff see it. */
export function partyScreening(overrides: Partial<PartyScreeningStaffResponse> = {}): PartyScreeningStaffResponse {
  return {
    party_id: 'p1',
    status: 'PENDING_REVIEW',
    last_screened_at: '2026-10-20T08:00:00Z',
    lists_covered: ['OFAC_SDN', 'MX_LPB'],
    open_finding_count: 1,
    findings: [matchSummary()],
    ...overrides,
  }
}

/** A party never screened: no status, no run, no lists, no findings. */
export function neverScreened(): PartyScreeningStaffResponse {
  return partyScreening({ status: null, last_screened_at: null, lists_covered: [], open_finding_count: 0, findings: [] })
}

/** The staff answer of a check with one alert (with its finding) and one discarded hit. */
export function checkResult(overrides: Partial<CheckStaffResponse> = {}): CheckStaffResponse {
  return {
    run_id: 'r3',
    subject_id: 's1',
    status: 'PENDING_REVIEW',
    screened_at: '2026-10-20T09:00:00Z',
    hits: [
      hit({ id: 'h3', match_id: 'm1' }),
      hit({ id: 'h4', list_code: 'UN_CONSOLIDATED', band: 'AUTO_DISCARDED', score: '0.4100', match_id: null }),
    ],
    ...overrides,
  }
}
