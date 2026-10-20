import type { TFunction } from 'i18next'

/**
 * Readers of a `ListEntry`'s canonical JSON (the library's serialization), shared by the shared
 * lists' entry sheet and the private lists' revealed entries. Every reader returns plain text.
 */

export type Json = Record<string, unknown>

export const isObject = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)
export const arrayOf = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
export const textOf = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null)
export const strings = (value: unknown): string[] => arrayOf(value).flatMap((item) => textOf(item) ?? [])

/** A name's full text (`EntryName.full`). */
export const nameOf = (value: unknown): string | null => (isObject(value) ? textOf(value.full) : null)

/** A `PartialDate` to the precision it is known: `1970`, `1970-03` or `1970-03-04`. */
export function partialDate(value: unknown): string | null {
  if (!isObject(value) || typeof value.year !== 'number') return null
  const pad = (n: number) => String(n).padStart(2, '0')
  const parts = [String(value.year)]
  if (typeof value.month === 'number') parts.push(pad(value.month))
  if (typeof value.month === 'number' && typeof value.day === 'number') parts.push(pad(value.day))
  return parts.join('-')
}

/** The library's `EntityType` and `IdType` values, each with a `lists.*` catalogue key. */
const ENTITY_TYPES = ['PERSON', 'ORGANIZATION', 'VESSEL', 'AIRCRAFT', 'OTHER'] as const
const ID_TYPES = [
  'RFC',
  'CURP',
  'PASSPORT',
  'NATIONAL_ID',
  'TAX_ID',
  'REGISTRATION',
  'IMO',
  'DIGITAL_CURRENCY_ADDRESS',
  'OTHER',
] as const

const isOneOf = <T extends string>(values: readonly T[], value: string): value is T =>
  (values as readonly string[]).includes(value)

/** An entity type in words; a value the console does not know yet shows as sent. */
export function entityType(t: TFunction, value: unknown): string | null {
  const raw = textOf(value)
  if (raw === null) return null
  return isOneOf(ENTITY_TYPES, raw) ? t(`lists.entityType.${raw}`) : raw
}

/** An identifier type in words; a value the console does not know yet shows as sent. */
function idType(t: TFunction, value: unknown): string {
  const raw = textOf(value)
  if (raw === null) return ''
  return isOneOf(ID_TYPES, raw) ? t(`lists.idType.${raw}`) : raw
}

export function identifier(t: TFunction, value: unknown): string | null {
  if (!isObject(value)) return null
  const shown = textOf(value.value)
  if (shown === null) return null
  const country = textOf(value.country)
  return `${idType(t, value.id_type)} ${shown}${country ? ` (${country})` : ''}`.trim()
}
