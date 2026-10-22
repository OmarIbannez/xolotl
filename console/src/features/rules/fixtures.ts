import type { ParamView, RuleResponse, SimulationResponse, TemplateResponse, VersionResponse } from '@/api/types'

export const RULE_ID = '0191d3a2-6b1c-7000-8000-0000000000r1'
export const AUTHOR_ID = '0191d3a2-6b1c-7000-8000-000000000001'
export const SENTENCE_ES =
  'Dispara cuando el cliente recibe 3 o más depósitos en efectivo entre 70 % y 100 % del umbral en 7 días y su suma alcanza el umbral'
export const SENTENCE_EN =
  'Fires when the customer receives 3 or more cash deposits between 70% and 100% of the threshold within 7 days and their sum reaches the threshold'

const SOURCE = 'template CASH_STRUCTURING v1'

/** CASH_STRUCTURING's parameters as the library ships them. */
export function structuringParams(): ParamView[] {
  return [
    { name: 'threshold', value: '5000', type: 'DECIMAL', min: '1000', max: null, choices: null, tighten: 'LOWER', unit: 'USD', source: SOURCE },
    { name: 'near_ratio', value: '0.70', type: 'DECIMAL', min: '0.50', max: '0.95', choices: null, tighten: null, unit: null, source: SOURCE },
    { name: 'window', value: 'P7D', type: 'STR', min: null, max: null, choices: null, tighten: null, unit: null, source: SOURCE },
    { name: 'min_count', value: '3', type: 'INT', min: '2', max: null, choices: null, tighten: null, unit: null, source: SOURCE },
  ]
}

export function template(overrides: Partial<TemplateResponse> = {}): TemplateResponse {
  return {
    code: 'CASH_STRUCTURING',
    family: 'ALERT',
    kind: 'STRUCTURING',
    title: 'Estructuración de depósitos en efectivo',
    typology: ['STRUCTURING'],
    country_code: 'MX',
    packs: ['MX/ITF-IFPE'],
    version: 1,
    params: structuringParams(),
    definition: {},
    sentences: { 'es-MX': SENTENCE_ES, en: SENTENCE_EN },
    ...overrides,
  }
}

/** A completed simulation of version 1, finished on 20 October 2026 and valid until 19 November. */
export function simulation(overrides: Partial<SimulationResponse> = {}): SimulationResponse {
  return {
    id: 's1',
    rule_version_id: 'v1',
    status: 'COMPLETED',
    range_start: '2026-04-21',
    range_end: '2026-10-20',
    definition_hash: 'ab12',
    transactions_read: 1200,
    hit_count: 4,
    customers_hit: 3,
    hits_per_week: [
      { week: '2026-W41', hits: 1, customers: 1 },
      { week: '2026-W42', hits: 3, customers: 2 },
    ],
    overlap: { existing_hits: 2, same_customers: 1, later_true_match: 0 },
    sample: [
      {
        rule_version_id: 'v1',
        party_id: 'party-1',
        relationship_id: 'rel-1',
        trigger_transaction_id: 'txn-1',
        dedup_key: 'RELATIONSHIP:rel-1:2026-10-12',
        window_start: '2026-10-12T00:00:00+00:00',
        window_end: '2026-10-19T00:00:00+00:00',
        severity: 'MEDIUM',
        outcome: 'ALERT',
        aggregates: {},
      },
    ],
    requested_by: AUTHOR_ID,
    started_at: '2026-10-20T15:00:00Z',
    finished_at: '2026-10-20T15:01:00Z',
    last_error: null,
    valid_until: '2026-11-19',
    notes: [],
    ...overrides,
  }
}

export function version(overrides: Partial<VersionResponse> = {}): VersionResponse {
  return {
    id: 'v1',
    rule_id: RULE_ID,
    version: 1,
    status: 'DRAFT',
    definition_hash: 'ab12',
    sync: false,
    params: structuringParams(),
    segment: null,
    severity: 'MEDIUM',
    dedup: { key: 'RELATIONSHIP', window: 'P7D', cooldown: 'P7D' },
    sentence: SENTENCE_ES,
    author_id: AUTHOR_ID,
    submitted_at: null,
    approved_by: null,
    approved_at: null,
    simulation: null,
    assignment: null,
    activated_at: null,
    retired_at: null,
    retired_by_version_id: null,
    allowed_actions: ['EDIT', 'SIMULATE'],
    blocked_reasons: {
      SUBMIT: ['STATE', 'SIMULATION_MISSING'],
      APPROVE: ['STATE', 'SIMULATION_MISSING'],
      REJECT: ['STATE'],
      ACTIVATE: ['STATE'],
      RETIRE: ['STATE'],
    },
    created_at: '2026-10-20T14:00:00Z',
    ...overrides,
  }
}

export function rule(overrides: Partial<RuleResponse> = {}): RuleResponse {
  return {
    id: RULE_ID,
    code: 'CASH_STRUCTURING',
    family: 'ALERT',
    kind: 'STRUCTURING',
    template_code: 'CASH_STRUCTURING',
    title: 'Estructuración de depósitos en efectivo',
    typology: ['STRUCTURING'],
    created_by: AUTHOR_ID,
    created_at: '2026-10-20T14:00:00Z',
    versions: [version()],
    active_version_id: null,
    last_simulation: null,
    ...overrides,
  }
}
