import type { Instrument, Rail, TransactionStatus } from '@/api/types'

export const STATUSES: readonly TransactionStatus[] = ['RECEIVED', 'PROCESSED', 'PARKED', 'BACKFILL']
export const INSTRUMENTS: readonly Instrument[] = ['CASH', 'TRANSFER', 'CARD', 'CHEQUE', 'CRYPTO', 'FX', 'FEE']
export const RAILS: readonly Rail[] = ['SPEI', 'CODI', 'CASH', 'CARD', 'WALLET', 'SWIFT', 'CRYPTO_ONCHAIN', 'OTHER']

/**
 * What the transactions list is narrowed to. `from` and `to` are Mexico City days
 * (`YYYY-MM-DD`, both included), as `GET /v1/transactions` reads them.
 */
export interface TransactionFilters {
  party_id?: string
  external_id?: string
  from?: string
  to?: string
  status?: TransactionStatus
  instrument?: Instrument
  rail?: Rail
  late?: boolean
}

const TEXT_KEYS = ['party_id', 'external_id', 'from', 'to', 'status', 'instrument', 'rail'] as const
const DAY = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** True for a party id the API can read (a UUID); the form and the URL both refuse anything else. */
export function isUuid(value: string): boolean {
  return UUID.test(value)
}

/**
 * The filters as query parameters: blank text dropped, the rest as sent (dates stay `YYYY-MM-DD`).
 * Typed as the filters themselves (each value a string or a boolean), so the result is both the
 * API's query and what the URL keeps.
 */
export function filtersToQuery(filters: TransactionFilters): TransactionFilters {
  const query: Record<string, string | boolean> = {}
  for (const key of TEXT_KEYS) {
    const value = filters[key]?.trim()
    if (value !== undefined && value !== '') query[key] = value
  }
  if (filters.late !== undefined) query.late = filters.late
  return query as TransactionFilters
}

function oneOf<T extends string>(values: readonly T[], value: string | null): T | undefined {
  return values.find((candidate) => candidate === value)
}

function text(value: string | null): string | undefined {
  const trimmed = value?.trim()
  return trimmed === undefined || trimmed === '' ? undefined : trimmed
}

function uuid(value: string | null): string | undefined {
  const trimmed = text(value)
  return trimmed !== undefined && isUuid(trimmed) ? trimmed : undefined
}

function day(value: string | null): string | undefined {
  return value !== null && DAY.test(value) ? value : undefined
}

/** The filters a link or a reload put in the URL; anything malformed is left out. */
export function filtersFromSearch(params: URLSearchParams): TransactionFilters {
  const late = params.get('late')
  const filters: TransactionFilters = {
    party_id: uuid(params.get('party_id')),
    external_id: text(params.get('external_id')),
    from: day(params.get('from')),
    to: day(params.get('to')),
    status: oneOf(STATUSES, params.get('status')),
    instrument: oneOf(INSTRUMENTS, params.get('instrument')),
    rail: oneOf(RAILS, params.get('rail')),
    late: late === 'true' ? true : late === 'false' ? false : undefined,
  }
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined)) as TransactionFilters
}

/**
 * False when `from` is after `to`. The API answers such a range with an empty page rather
 * than an error, so the console refuses it itself instead of showing "no transactions".
 */
export function rangeIsValid(filters: Pick<TransactionFilters, 'from' | 'to'>): boolean {
  return filters.from === undefined || filters.to === undefined || filters.from <= filters.to
}
