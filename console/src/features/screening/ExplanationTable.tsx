import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { HitView } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { KeyValues } from '@/components/common/page'
import { arrayOf, isObject, strings, textOf, type Json } from '@/features/lists/entry-json'
import { BandBadge } from './BandBadge'
import { MATCH_KINDS, QUALIFIER_ATTRIBUTES, QUALIFIER_OUTCOMES, RULE_CODES } from './explanation-codes'

/**
 * Readers of a hit's stored explanation (`screening_hit.features`, spec section 8.4): the
 * library's `MatchExplanation.to_json()` plus the thresholds, the list version and the matcher
 * version. The query side holds normalized tokens only. Codes are translated, never shown raw.
 */

const isOneOf = <T extends string>(values: readonly T[], value: unknown): value is T =>
  typeof value === 'string' && (values as readonly string[]).includes(value)

const numberOf = (value: unknown): number | null => (typeof value === 'number' ? value : null)

interface Alignment {
  key: number
  query: string
  entry: string
  kind: unknown
  similarity: number | null
  weight: number | null
}

interface Qualifier {
  key: number
  attribute: unknown
  outcome: unknown
  adjustment: number | null
}

function alignments(features: Json): Alignment[] {
  return arrayOf(features.alignment).flatMap((item, key) =>
    isObject(item)
      ? [
          {
            key,
            query: textOf(item.query) ?? '',
            entry: textOf(item.entry) ?? '',
            kind: item.kind,
            similarity: numberOf(item.similarity),
            weight: numberOf(item.weight),
          },
        ]
      : [],
  )
}

function qualifiers(features: Json): Qualifier[] {
  return arrayOf(features.qualifiers).flatMap((item, key) =>
    isObject(item) ? [{ key, attribute: item.attribute, outcome: item.outcome, adjustment: numberOf(item.adjustment) }] : [],
  )
}

const fixed = (value: number | null, digits = 4) => (value === null ? '—' : value.toFixed(digits))
const signed = (value: number | null) => (value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(4)}`)

/** The aligned tokens, the unmatched ones, the qualifiers, the rules and the score of one hit. */
export function ExplanationTable({ hit }: { hit: HitView }) {
  const { t } = useTranslation()
  const features: Json = hit.features
  const thresholds = isObject(features.thresholds) ? features.thresholds : {}
  const entryRef = isObject(features.entry_ref) ? features.entry_ref : {}
  const none = <span className="text-muted-foreground">{t('screening.match.explanation.none')}</span>

  const kindLabel = (kind: unknown): string =>
    isOneOf(MATCH_KINDS, kind) ? t(`screening.match.explanation.kinds.${kind}`) : '—'
  const outcomeLabel = (outcome: unknown): string =>
    isOneOf(QUALIFIER_OUTCOMES, outcome) ? t(`screening.match.qualifiers.outcomes.${outcome}`) : '—'
  const attributeLabel = (attribute: unknown): string =>
    isOneOf(QUALIFIER_ATTRIBUTES, attribute) ? t(`screening.match.qualifiers.attributes.${attribute}`) : '—'

  const tokens = (values: string[]): ReactNode =>
    values.length === 0 ? (
      none
    ) : (
      <span className="font-mono text-xs">{values.join(' · ')}</span>
    )

  const alignmentColumns: Column<Alignment>[] = [
    { id: 'query', header: t('screening.match.explanation.query'), cell: (row) => <span className="font-mono text-xs">{row.query}</span> },
    { id: 'entry', header: t('screening.match.explanation.entry'), cell: (row) => <span className="font-mono text-xs">{row.entry}</span> },
    { id: 'kind', header: t('screening.match.explanation.kind'), cell: (row) => kindLabel(row.kind) },
    {
      id: 'similarity',
      header: t('screening.match.explanation.similarity'),
      cell: (row) => <span className="tabular">{fixed(row.similarity)}</span>,
      className: 'text-right',
    },
    {
      id: 'weight',
      header: t('screening.match.explanation.weight'),
      cell: (row) => <span className="tabular">{fixed(row.weight)}</span>,
      className: 'text-right',
    },
  ]

  const qualifierColumns: Column<Qualifier>[] = [
    { id: 'attribute', header: t('screening.match.qualifiers.attribute'), cell: (row) => attributeLabel(row.attribute) },
    { id: 'outcome', header: t('screening.match.qualifiers.outcome'), cell: (row) => outcomeLabel(row.outcome) },
    {
      id: 'adjustment',
      header: t('screening.match.qualifiers.adjustment'),
      cell: (row) => <span className="tabular">{signed(row.adjustment)}</span>,
      className: 'text-right',
    },
  ]

  const rules = arrayOf(features.rules).flatMap((item) => (isObject(item) && isOneOf(RULE_CODES, item.code) ? [item.code] : []))
  const versionSeq = numberOf(entryRef.list_version_seq)

  return (
    <div className="space-y-6">
      <DataTable
        label={t('screening.match.explanation.label')}
        columns={alignmentColumns}
        rows={alignments(features)}
        getRowId={(row) => String(row.key)}
        empty={t('screening.match.explanation.empty')}
        loadingRows={2}
      />
      <KeyValues
        items={[
          [t('screening.match.explanation.unmatchedQuery'), tokens(strings(features.unmatched_query))],
          [t('screening.match.explanation.unmatchedEntry'), tokens(strings(features.unmatched_entry))],
        ]}
      />

      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('screening.match.qualifiers.title')}</h3>
        <DataTable
          label={t('screening.match.qualifiers.label')}
          columns={qualifierColumns}
          rows={qualifiers(features)}
          getRowId={(row) => String(row.key)}
          empty={t('screening.match.qualifiers.empty')}
          loadingRows={2}
        />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('screening.match.rules.title')}</h3>
        {rules.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('screening.match.rules.empty')}</p>
        ) : (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {rules.map((code) => (
              <li key={code}>{t(`screening.match.rules.codes.${code}`)}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('screening.match.thresholds')}</h3>
        <KeyValues
          items={[
            [t('screening.match.score'), <span key="score" className="tabular">{hit.score}</span>],
            [t('screening.match.band'), <BandBadge key="band" band={hit.band} />],
            [t('screening.match.alertThreshold'), <span key="alert" className="tabular">{textOf(thresholds.alert) ?? '—'}</span>],
            [t('screening.match.reviewThreshold'), <span key="review" className="tabular">{textOf(thresholds.review) ?? '—'}</span>],
            [t('screening.match.explanation.nameScore'), <span key="name" className="tabular">{fixed(numberOf(features.name_score))}</span>],
            [t('screening.match.list'), <span key="list" className="font-mono text-xs">{hit.list_code}</span>],
            [t('screening.match.versionSeq'), <span key="seq" className="tabular">{versionSeq ?? '—'}</span>],
            [t('screening.match.matcherVersion'), <span key="matcher" className="font-mono text-xs">{textOf(features.matcher_version) ?? '—'}</span>],
          ]}
        />
      </section>
    </div>
  )
}
