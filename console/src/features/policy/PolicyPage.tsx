import { CircleAlert, CircleCheck, TriangleAlert } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { hasProblemCode } from '@/api/errors'
import type { Confidence, ConversionResponse, EffectivePolicyResponse } from '@/api/types'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { Field } from '@/components/common/Field'
import { KeyValues, PageHeader } from '@/components/common/page'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { TruncatedId } from '@/features/audit/TruncatedId'
import { useFormat } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import { describeConversion, formatQuantity, provenanceOf } from './provenance'
import { useAssignments, useEffectivePolicy, useOverrides, usePackVersions } from './queries'

type Policy = EffectivePolicyResponse['policy']
type Tier = Policy['kyc']['tiers'][number]
type Capture = Policy['captures'][number]
type Assignment = NonNullable<ReturnType<typeof useAssignments>['data']>['items'][number]
type Override = NonNullable<ReturnType<typeof useOverrides>['data']>['items'][number]

/** The fields every provenanced value carries. */
interface ProvenancedValue {
  confidence: Confidence
  legal_basis: string
  verified_as_of: string
}

/** Characters of the snapshot hash shown; the whole hash is in the title. */
const HASH_CHARS = 12

/** Today's calendar date where the viewer is, as YYYY-MM-DD. */
function localToday(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/** VERIFIED or UNVERIFIED, with the legal basis and the verification date on hover. */
function ProvenanceBadge({ value }: { value: ProvenancedValue | null | undefined }) {
  const { t } = useTranslation()
  if (value === null || value === undefined) return null
  const { confidence, legalBasis, verifiedAsOf } = provenanceOf(value)
  const Icon = confidence === 'VERIFIED' ? CircleCheck : CircleAlert
  return (
    <Badge variant={confidence === 'VERIFIED' ? 'good' : 'warning'} title={`${legalBasis} · ${verifiedAsOf}`}>
      <Icon aria-hidden />
      {t(`policy.provenance.${confidence}`)}
    </Badge>
  )
}

/** A value with its provenance badge beside it, for the key-value and list sections. */
function WithProvenance({ value, children }: { value: ProvenancedValue; children: ReactNode }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span>{children}</span>
      <ProvenanceBadge value={value} />
    </span>
  )
}

function ConversionText({ conversion }: { conversion: ConversionResponse | null | undefined }) {
  const { t } = useTranslation()
  const format = useFormat()
  const missing = conversion === null || conversion === undefined
  return (
    <span className={cn('tabular', missing && 'text-muted-foreground')} title={missing ? t('policy.noConversion') : undefined}>
      {describeConversion(conversion, format)}
    </span>
  )
}

function Section({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <Card className="animate-fade-in-up">
      {title !== undefined && (
        <CardHeader>
          <CardTitle>{title}</CardTitle>
        </CardHeader>
      )}
      <CardContent>{children}</CardContent>
    </Card>
  )
}

/** The pack codes in force (with their titles), the override version and the snapshot hash. */
function Header({ data }: { data: EffectivePolicyResponse }) {
  const { t } = useTranslation()
  const versions = usePackVersions()
  const titles = new Map((versions.data?.items ?? []).map((version) => [version.id, version.title]))
  const { policy } = data
  return (
    <Section title={t('policy.packs')}>
      <div className="space-y-4">
        <ul className="space-y-1">
          {policy.pack_codes.map((code, index) => {
            const title = titles.get(policy.pack_version_ids[index] ?? '')
            return (
              <li key={code} className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono text-sm font-medium">{code}</span>
                {title !== undefined && <span className="text-sm text-muted-foreground">{title}</span>}
              </li>
            )
          })}
        </ul>
        <KeyValues
          items={[
            [t('policy.overrideVersion'), policy.override_version ?? '—'],
            [
              t('policy.snapshot'),
              <span key="hash" className="font-mono text-xs" title={data.content_hash}>
                {data.content_hash.slice(0, HASH_CHARS)}
              </span>,
            ],
          ]}
        />
      </div>
    </Section>
  )
}

function TiersTable({ data }: { data: EffectivePolicyResponse }) {
  const { t } = useTranslation()
  const converted = (tier: Tier, name: 'monthly_ops' | 'max_balance') =>
    data.converted[`kyc.tiers[${tier.code}].limits.${name}`]
  // The provenance sits in its own column, so a limit's cell reads as the limit alone.
  const columns: Column<Tier>[] = [
    { id: 'code', header: t('policy.tiers.code'), cell: (tier) => <span className="font-mono">{tier.code}</span> },
    {
      id: 'monthlyOps',
      header: t('policy.tiers.monthlyOps'),
      cell: (tier) => (tier.limits.monthly_ops ? formatQuantity(tier.limits.monthly_ops.value) : '—'),
      className: 'tabular',
    },
    {
      id: 'monthlyOpsBasis',
      header: t('policy.provenance.basis'),
      cell: (tier) => <ProvenanceBadge value={tier.limits.monthly_ops} />,
    },
    {
      id: 'monthlyOpsConverted',
      header: t('policy.tiers.converted'),
      cell: (tier) => (tier.limits.monthly_ops ? <ConversionText conversion={converted(tier, 'monthly_ops')} /> : null),
    },
    {
      id: 'maxBalance',
      header: t('policy.tiers.maxBalance'),
      cell: (tier) => (tier.limits.max_balance ? formatQuantity(tier.limits.max_balance.value) : '—'),
      className: 'tabular',
    },
    {
      id: 'maxBalanceBasis',
      header: t('policy.provenance.basis'),
      cell: (tier) => <ProvenanceBadge value={tier.limits.max_balance} />,
    },
    {
      id: 'maxBalanceConverted',
      header: t('policy.tiers.converted'),
      cell: (tier) => (tier.limits.max_balance ? <ConversionText conversion={converted(tier, 'max_balance')} /> : null),
    },
    { id: 'assurance', header: t('policy.tiers.assurance'), cell: (tier) => tier.assurance },
  ]
  return (
    <DataTable
      label={t('policy.tiers.title')}
      columns={columns}
      rows={data.policy.kyc.tiers}
      getRowId={(tier) => tier.code}
      empty="—"
    />
  )
}

function CapturesTable({ data }: { data: EffectivePolicyResponse }) {
  const { t } = useTranslation()
  const columns: Column<Capture>[] = [
    { id: 'code', header: t('policy.captures.code'), cell: (capture) => <span className="font-mono">{capture.code}</span> },
    { id: 'kind', header: t('policy.captures.kind'), cell: (capture) => capture.kind },
    {
      id: 'threshold',
      header: t('policy.captures.threshold'),
      cell: (capture) => formatQuantity(capture.threshold.value),
      className: 'tabular',
    },
    {
      id: 'thresholdBasis',
      header: t('policy.provenance.basis'),
      cell: (capture) => <ProvenanceBadge value={capture.threshold} />,
    },
    { id: 'rule', header: t('policy.captures.rule'), cell: (capture) => capture.conversion_rule },
    { id: 'period', header: t('policy.captures.period'), cell: (capture) => capture.period.value },
    {
      id: 'periodBasis',
      header: t('policy.provenance.basis'),
      cell: (capture) => <ProvenanceBadge value={capture.period} />,
    },
    { id: 'report', header: t('policy.captures.report'), cell: (capture) => capture.report },
    { id: 'filing', header: t('policy.captures.filing'), cell: (capture) => capture.filing },
    {
      id: 'converted',
      header: t('policy.tiers.converted'),
      cell: (capture) => <ConversionText conversion={data.converted[`captures[${capture.code}].threshold`]} />,
    },
  ]
  return (
    <DataTable
      label={t('policy.captures.title')}
      columns={columns}
      rows={data.policy.captures}
      getRowId={(capture) => capture.code}
      empty="—"
    />
  )
}

function EffectiveView({ data }: { data: EffectivePolicyResponse }) {
  const { t } = useTranslation()
  const { policy } = data
  const yesNo = (value: boolean) => (value ? t('common.yes') : t('common.no'))
  return (
    <div className="space-y-6">
      {data.missing_units.length > 0 && (
        <p
          data-slot="missing-units"
          className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <span>{t('policy.missingUnits', { units: data.missing_units.join(', ') })}</span>
        </p>
      )}

      <Header data={data} />

      <Section title={t('policy.tiers.title')}>
        <TiersTable data={data} />
      </Section>

      <Section title={t('policy.captures.title')}>
        <CapturesTable data={data} />
      </Section>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* The two list kinds label themselves. */}
        <Section>
          <KeyValues
            items={[
              [
                t('policy.lists.mandatory'),
                <ul key="mandatory" className="flex flex-wrap gap-2">
                  {policy.screening.mandatory_lists.map((list) => (
                    <li key={list.value}>
                      <WithProvenance value={list}>
                        <Badge variant="mono">{list.value}</Badge>
                      </WithProvenance>
                    </li>
                  ))}
                </ul>,
              ],
              [
                t('policy.lists.recommended'),
                <ul key="recommended" className="flex flex-wrap gap-1">
                  {policy.screening.recommended_lists.map((list) => (
                    <li key={list}>
                      <Badge variant="mono">{list}</Badge>
                    </li>
                  ))}
                </ul>,
              ],
            ]}
          />
        </Section>

        <Section title={t('policy.retention.title')}>
          <KeyValues
            items={[
              [
                t('policy.retention.kyc'),
                <WithProvenance key="kyc" value={policy.retention.kyc_after_relationship}>
                  <span className="font-mono">{policy.retention.kyc_after_relationship.value}</span>
                </WithProvenance>,
              ],
              [
                t('policy.retention.transactions'),
                <WithProvenance key="transactions" value={policy.retention.transactions}>
                  <span className="font-mono">{policy.retention.transactions.value}</span>
                </WithProvenance>,
              ],
            ]}
          />
        </Section>

        <Section title={t('policy.governance.title')}>
          <KeyValues
            items={[
              [
                t('policy.governance.committee'),
                <WithProvenance key="committee" value={policy.governance.committee_required}>
                  {yesNo(policy.governance.committee_required.value)}
                </WithProvenance>,
              ],
              [t('policy.governance.fourEyes'), yesNo(policy.governance.four_eyes_on_reports ?? true)],
            ]}
          />
        </Section>
      </div>
    </div>
  )
}

function AssignmentsCard() {
  const { t } = useTranslation()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const assignments = useAssignments(cursor)
  const page = assignments.data
  const columns: Column<Assignment>[] = [
    { id: 'pack', header: t('policy.assignments.pack'), cell: (row) => <span className="font-mono">{row.pack}</span> },
    { id: 'version', header: t('policy.assignments.version'), cell: (row) => row.version, className: 'tabular' },
    {
      id: 'product',
      header: t('policy.assignments.product'),
      cell: (row) =>
        row.product ?? <span className="text-muted-foreground">{t('policy.assignments.allProducts')}</span>,
    },
    { id: 'from', header: t('policy.assignments.from'), cell: (row) => row.effective_from, className: 'tabular' },
    { id: 'to', header: t('policy.assignments.to'), cell: (row) => row.effective_to ?? '—', className: 'tabular' },
    { id: 'approvedBy', header: t('policy.assignments.approvedBy'), cell: (row) => <TruncatedId value={row.approved_by} /> },
    { id: 'createdAt', header: t('policy.assignments.createdAt'), cell: (row) => <DateTimeCell value={row.created_at} /> },
  ]
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('policy.assignments.title')}</CardTitle>
      </CardHeader>
      <CardContent>
        {assignments.isError ? (
          <ErrorBlock error={assignments.error} onRetry={() => void assignments.refetch()} />
        ) : (
          <div className={cn(assignments.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <DataTable
              label={t('policy.assignments.title')}
              columns={columns}
              rows={page?.items}
              getRowId={(row) => row.id}
              loading={assignments.isPending}
              empty={t('policy.assignments.empty')}
            />
          </div>
        )}
      </CardContent>
      {showPager && (
        <CardFooter>
          <div className="w-full">
            <CursorPager
              cursor={cursor}
              nextCursor={page?.next_cursor}
              onChange={setCursor}
              busy={assignments.isPlaceholderData}
            />
          </div>
        </CardFooter>
      )}
    </Card>
  )
}

/** The names of the values an override version sets, in the order it writes them. */
function overriddenKeys(row: Override): string {
  const set = Object.entries(row.overrides).filter(([, value]) => {
    if (value === null || value === undefined) return false
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'object') return Object.keys(value).length > 0
    return true
  })
  return set.map(([key]) => key).join(', ')
}

function OverridesCard() {
  const { t } = useTranslation()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const overrides = useOverrides(cursor)
  const page = overrides.data
  const columns: Column<Override>[] = [
    { id: 'version', header: t('policy.overrides.version'), cell: (row) => row.version, className: 'tabular' },
    { id: 'from', header: t('policy.overrides.from'), cell: (row) => row.effective_from, className: 'tabular' },
    { id: 'approvedBy', header: t('policy.overrides.approvedBy'), cell: (row) => <TruncatedId value={row.approved_by} /> },
    {
      id: 'keys',
      header: t('policy.overrides.keys'),
      cell: (row) => <span className="font-mono text-xs">{overriddenKeys(row)}</span>,
      className: 'whitespace-normal',
    },
  ]
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('policy.overrides.title')}</CardTitle>
      </CardHeader>
      <CardContent>
        {overrides.isError ? (
          <ErrorBlock error={overrides.error} onRetry={() => void overrides.refetch()} />
        ) : (
          <div className={cn(overrides.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <DataTable
              label={t('policy.overrides.title')}
              columns={columns}
              rows={page?.items}
              getRowId={(row) => row.id}
              loading={overrides.isPending}
              empty={t('policy.overrides.empty')}
            />
          </div>
        )}
      </CardContent>
      {showPager && (
        <CardFooter>
          <div className="w-full">
            <CursorPager
              cursor={cursor}
              nextCursor={page?.next_cursor}
              onChange={setCursor}
              busy={overrides.isPlaceholderData}
            />
          </div>
        </CardFooter>
      )}
    </Card>
  )
}

interface Filters {
  on: string
  product: string
}

/** The pack viewer: the effective policy for a date and product, then assignments and overrides. */
export function PolicyPage() {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<Filters>(() => ({ on: localToday(), product: '' }))
  const [applied, setApplied] = useState<Filters>(draft)
  const product = applied.product.trim()
  const policy = useEffectivePolicy(applied.on, product === '' ? undefined : product)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (draft.on === '') return
    setApplied(draft)
  }

  let effective: ReactNode
  if (policy.isError) {
    effective = hasProblemCode(policy.error, 'POLICY_NOT_ASSIGNED') ? (
      <EmptyBlock>{t('policy.notAssigned')}</EmptyBlock>
    ) : (
      <ErrorBlock error={policy.error} onRetry={() => void policy.refetch()} />
    )
  } else if (policy.data === undefined) {
    effective = <LoadingBlock rows={6} />
  } else {
    effective = (
      <div className={cn(policy.isPlaceholderData && 'opacity-60 transition-opacity')}>
        <EffectiveView data={policy.data} />
      </div>
    )
  }

  return (
    <>
      <PageHeader title={t('policy.title')} description={t('policy.description')} />

      <div className="space-y-6">
        <Card className="animate-fade-in-up">
          <CardContent>
            <form role="search" onSubmit={submit} noValidate className="flex flex-wrap items-end gap-3">
              <div className="w-44">
                <Field label={t('policy.filters.on')}>
                  {(control) => (
                    <Input
                      {...control}
                      type="date"
                      required
                      value={draft.on}
                      onChange={(event) => setDraft({ ...draft, on: event.target.value })}
                    />
                  )}
                </Field>
              </div>
              <div className="w-48">
                <Field label={t('policy.filters.product')}>
                  {(control) => (
                    <Input
                      {...control}
                      value={draft.product}
                      maxLength={64}
                      autoComplete="off"
                      autoCapitalize="characters"
                      spellCheck={false}
                      className="font-mono"
                      onChange={(event) => setDraft({ ...draft, product: event.target.value })}
                    />
                  )}
                </Field>
              </div>
              <Button type="submit" disabled={policy.isFetching}>
                {t('policy.filters.apply')}
              </Button>
            </form>
          </CardContent>
        </Card>

        {effective}

        <AssignmentsCard />
        <OverridesCard />
      </div>
    </>
  )
}
