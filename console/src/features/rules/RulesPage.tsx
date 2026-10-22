import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import type { RuleResponse, RuleVersionStatus, TemplateResponse } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Field } from '@/components/common/Field'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { FormAlert } from '@/features/auth/FormAlert'
import { TypologyTags } from '@/features/hits/SeverityBadge'
import { useLocale } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import { useCreateRule, useRules, useTemplates } from './queries'
import { RuleStatusBadge, SimulationAgeBadge } from './RuleStatusBadge'

const TABS = ['library', 'tenant'] as const
type Tab = (typeof TABS)[number]
const isTab = (value: string | null): value is Tab => (TABS as readonly (string | null)[]).includes(value)

const STATUSES: readonly RuleVersionStatus[] = ['DRAFT', 'SIMULATED', 'IN_REVIEW', 'APPROVED', 'ACTIVE', 'RETIRED']
const isStatus = (value: string): value is RuleVersionStatus => (STATUSES as readonly string[]).includes(value)
const ALL = 'ALL'

/** A template's sentence in the active language, or in Spanish when it has none in it. */
function sentenceOf(template: TemplateResponse, locale: string): string {
  return template.sentences[locale] ?? template.sentences['es-MX'] ?? ''
}

/** The library: every template with its kind, typologies and sentence; writers create a rule from one. */
function LibraryTab() {
  const { t } = useTranslation()
  const locale = useLocale()
  const navigate = useNavigate()
  const { hasRole } = useAuth()
  const templates = useTemplates()
  const create = useCreateRule()
  const [creating, setCreating] = useState<string | undefined>(undefined)
  const canWrite = hasRole(ACCESS.rulesWrite)

  function createFrom(code: string) {
    setCreating(code)
    create.mutate(
      { template_code: code },
      { onSuccess: ({ data }) => void navigate(`/rules/monitoring/${data.rule_id}`) },
    )
  }

  const columns: Column<TemplateResponse>[] = [
    {
      id: 'rule',
      header: t('rules.table.rule'),
      cell: (row) => (
        <div className="space-y-0.5">
          <span className="block font-mono text-xs">{row.code}</span>
          <span className="block text-xs text-muted-foreground">{row.title}</span>
        </div>
      ),
    },
    { id: 'kind', header: t('rules.table.kind'), cell: (row) => <Badge variant="outline">{t(`rules.kind.${row.kind}`)}</Badge> },
    { id: 'typology', header: t('rules.table.typology'), cell: (row) => <TypologyTags typology={row.typology} /> },
    {
      id: 'sentence',
      header: t('rules.table.sentence'),
      cell: (row) => <p className="max-w-xl text-sm whitespace-normal">{sentenceOf(row, locale)}</p>,
    },
    ...(canWrite
      ? [
          {
            id: 'create',
            header: <span className="sr-only">{t('rules.create')}</span>,
            cell: (row: TemplateResponse) => (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={create.isPending}
                aria-label={t('rules.createFrom', { code: row.code })}
                onClick={() => createFrom(row.code)}
              >
                {create.isPending && creating === row.code ? t('rules.creating') : t('rules.create')}
              </Button>
            ),
          },
        ]
      : []),
  ]

  if (templates.isError) return <ErrorBlock error={templates.error} onRetry={() => void templates.refetch()} />
  return (
    <div className="space-y-3">
      {create.isError && <FormAlert error={create.error} />}
      <DataTable
        label={t('rules.table.libraryLabel')}
        columns={columns}
        rows={templates.data}
        getRowId={(row) => row.code}
        loading={templates.isPending}
        empty={t('rules.libraryEmpty')}
      />
    </div>
  )
}

/** The newest version: the rule's working state. */
function latestOf(rule: RuleResponse) {
  return rule.versions.at(-1)
}

/** The tenant's rules: each with its latest version's status, the active version and the last simulation. */
function TenantTab() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<RuleVersionStatus | undefined>(undefined)
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const rules = useRules({ status }, cursor)
  const page = rules.data
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  const columns: Column<RuleResponse>[] = [
    {
      id: 'rule',
      header: t('rules.table.rule'),
      cell: (row) => (
        <div className="space-y-0.5">
          <Link to={`/rules/monitoring/${row.id}`} className="block font-mono text-xs underline-offset-4 hover:underline">
            {row.code}
          </Link>
          <span className="block text-xs text-muted-foreground">{row.title}</span>
        </div>
      ),
    },
    { id: 'kind', header: t('rules.table.kind'), cell: (row) => <Badge variant="outline">{t(`rules.kind.${row.kind}`)}</Badge> },
    {
      id: 'latest',
      header: t('rules.table.latest'),
      cell: (row) => {
        const latest = latestOf(row)
        return latest === undefined ? (
          '—'
        ) : (
          <span className="flex items-center gap-2">
            <span className="tabular text-xs">v{latest.version}</span>
            <RuleStatusBadge status={latest.status} />
          </span>
        )
      },
    },
    {
      id: 'active',
      header: t('rules.table.active'),
      cell: (row) => {
        const active = row.versions.find((version) => version.id === row.active_version_id)
        return active === undefined ? <span className="text-muted-foreground">{t('rules.table.noActive')}</span> : `v${active.version}`
      },
    },
    {
      id: 'simulation',
      header: t('rules.table.simulation'),
      cell: (row) =>
        row.last_simulation === null ? (
          <span className="text-muted-foreground">{t('rules.table.noSimulation')}</span>
        ) : row.last_simulation.status === 'COMPLETED' ? (
          <SimulationAgeBadge simulation={row.last_simulation} />
        ) : (
          <span className="text-xs">{t(`rules.simulationStatus.${row.last_simulation.status}`)}</span>
        ),
    },
  ]

  return (
    <div className="space-y-4">
      <div className="max-w-xs">
        <Field label={t('rules.filters.status')}>
          {(control) => (
            <Select
              value={status ?? ALL}
              onValueChange={(value) => {
                setStatus(isStatus(value) ? value : undefined)
                setCursor(undefined)
              }}
            >
              <SelectTrigger {...control} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t('rules.filters.statusAll')}</SelectItem>
                {STATUSES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`rules.status.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      {rules.isError ? (
        <ErrorBlock error={rules.error} onRetry={() => void rules.refetch()} />
      ) : (
        <div className={cn(rules.isPlaceholderData && 'opacity-60 transition-opacity')}>
          <DataTable
            label={t('rules.table.tenantLabel')}
            columns={columns}
            rows={page?.items}
            getRowId={(row) => row.id}
            loading={rules.isPending}
            empty={t('rules.empty')}
          />
        </div>
      )}
      {showPager && <CursorPager cursor={cursor} nextCursor={page?.next_cursor} onChange={setCursor} busy={rules.isPlaceholderData} />}
    </div>
  )
}

/**
 * Reglas › Monitoreo: the typology library (the default tab) and the tenant's rules. A writer
 * creates a rule from a template and lands on its page, where versions are tuned, simulated and
 * moved through the lifecycle.
 */
export function RulesPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: Tab = isTab(requested) ? requested : 'library'

  return (
    <>
      <PageHeader title={t('rules.title')} description={t('rules.description')} />
      <Card className="animate-fade-in-up">
        <CardHeader>
          <Tabs value={tab} onValueChange={(value) => isTab(value) && setParams(value === 'library' ? {} : { tab: value })}>
            <TabsList aria-label={t('rules.tabs.label')}>
              {TABS.map((value) => (
                <TabsTrigger key={value} value={value}>
                  {t(`rules.tabs.${value}`)}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </CardHeader>
        <CardContent>{tab === 'library' ? <LibraryTab /> : <TenantTab />}</CardContent>
      </Card>
    </>
  )
}
