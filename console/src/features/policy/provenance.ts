import type { Confidence, ConversionResponse, Quantity } from '@/api/types'
import type { Locale } from '@/i18n'
import type { useFormat } from '@/i18n/use-locale'

/** "750 UDI": the amount exactly as the pack writes it, then its unit. */
export function formatQuantity(q: Quantity): string {
  return `${q.amount} ${q.unit}`
}

const moneyFormats = new Map<Locale, Intl.NumberFormat>()

function money(amount: string, locale: Locale): string {
  let format = moneyFormats.get(locale)
  if (format === undefined) {
    format = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    moneyFormats.set(locale, format)
  }
  return format.format(Number(amount))
}

/**
 * "6,390.00 MXN · UDI 2025-12-31": the converted amount, its currency, and the unit and day
 * whose value converted it. A dash when the API converted nothing (a unit value is missing).
 */
export function describeConversion(
  c: ConversionResponse | null | undefined,
  format: ReturnType<typeof useFormat>,
): string {
  if (c === null || c === undefined) return '—'
  return `${money(c.amount, format.locale)} ${c.currency} · ${c.unit} ${c.resolved_date}`
}

export interface Provenance {
  confidence: Confidence
  legalBasis: string
  verifiedAsOf: string
}

/** The provenance of a regulatory value, from the fields every `Provenanced` value carries. */
export function provenanceOf(p: { confidence: Confidence; legal_basis: string; verified_as_of: string }): Provenance {
  return { confidence: p.confidence, legalBasis: p.legal_basis, verifiedAsOf: p.verified_as_of }
}
