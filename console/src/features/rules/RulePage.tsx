import { ArrowLeft, Plus } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { isApiError } from '@/api/errors'
import type { RuleResponse, VersionResponse } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { KeyValues, PageHeader } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { SeverityBadge, TypologyTags } from '@/features/hits/SeverityBadge'
import { useFormat } from '@/i18n/use-locale'
import { cn } from '@/lib/utils'
import { NotFound } from '@/NotFound'
import { LifecycleButtons } from './LifecycleButtons'
import { useCreateVersion, useRule } from './queries'
import { RuleStatusBadge } from './RuleStatusBadge'
import { SimulationPanel } from './SimulationPanel'
import { VersionEditor } from './VersionEditor'

/** The rule's versions, newest first: each one selects itself for the panels below. */
function VersionsCard({
  rule,
  selected,
  onSelect,
}: {
  rule: RuleResponse
  selected: number
  onSelect: (version: number) => void
}) {
  const { t } = useTranslation()
  const format = useFormat()
  const { hasRole } = useAuth()
  const create = useCreateVersion(rule.id)
  const newestFirst = [...rule.versions].reverse()

  function createVersion() {
    create.mutate({}, { onSuccess: ({ data }) => onSelect(data.version) })
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="space-y-1.5">
          <CardTitle>{t('rules.versions.title')}</CardTitle>
          <CardDescription>{t('rules.versions.description')}</CardDescription>
        </div>
        {hasRole(ACCESS.rulesWrite) && (
          <Button type="button" variant="outline" size="sm" disabled={create.isPending} onClick={createVersion}>
            <Plus aria-hidden />
            {t('rules.versions.create')}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {create.isError && <FormAlert error={create.error} />}
        <ol aria-label={t('rules.versions.title')} className="space-y-2 border-l pl-4">
          {newestFirst.map((version) => (
            <li key={version.id}>
              <button
                type="button"
                aria-pressed={version.version === selected}
                onClick={() => onSelect(version.version)}
                className={cn(
                  'flex w-full flex-wrap items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted',
                  version.version === selected && 'bg-muted font-medium',
                )}
              >
                <span className="tabular">{t('rules.versions.version', { version: version.version })}</span>
                <RuleStatusBadge status={version.status} />
                {version.id === rule.active_version_id && <Badge variant="good">{t('rules.versions.inForce')}</Badge>}
                <span className="text-xs text-muted-foreground">{format.dateTime(version.created_at)}</span>
              </button>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  )
}

/** Where the version stands: its status, the four-eyes steps taken and its assignment. */
function VersionStateCard({ ruleId, version }: { ruleId: string; version: VersionResponse }) {
  const { t } = useTranslation()
  const format = useFormat()
  const when = (value: string | null) => (value === null ? '—' : format.dateTime(value))
  const assignment = version.assignment
  const rows: [string, ReactNode][] = [
    [t('rules.state.status'), <RuleStatusBadge key="status" status={version.status} />],
    [t('rules.editor.severity'), <SeverityBadge key="severity" severity={version.severity} />],
    [t('rules.state.submittedAt'), when(version.submitted_at)],
    [t('rules.state.approvedAt'), when(version.approved_at)],
    [t('rules.state.activatedAt'), when(version.activated_at)],
    [t('rules.state.retiredAt'), when(version.retired_at)],
  ]
  if (assignment !== null) {
    rows.push([
      t('rules.state.assignment'),
      t('rules.state.assignmentText', {
        from: format.date(`${assignment.effective_from}T12:00:00Z`),
        to: assignment.effective_to === null ? '—' : format.date(`${assignment.effective_to}T12:00:00Z`),
        product: assignment.product ?? t('rules.state.everyProduct'),
      }),
    ])
  }
  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('rules.state.title')}</CardTitle>
        <CardDescription>{t('rules.state.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <KeyValues items={rows} />
        {/* Keyed by the version, so a refusal or an open dialog never carries over to another. */}
        <LifecycleButtons key={version.id} ruleId={ruleId} version={version} />
      </CardContent>
    </Card>
  )
}

function RuleDetailView({ ruleId }: { ruleId: string }) {
  const { t } = useTranslation()
  const rule = useRule(ruleId)
  const [chosen, setChosen] = useState<number | undefined>(undefined)
  // Whether the editor holds changes not saved yet, which a simulation would not see.
  const [unsaved, setUnsaved] = useState(false)

  if (isApiError(rule.error) && rule.error.status === 404) return <NotFound />

  let body: ReactNode
  let title = t('rules.detail.title')
  if (rule.isError) body = <ErrorBlock error={rule.error} onRetry={() => void rule.refetch()} />
  else if (rule.data === undefined) body = <LoadingBlock rows={6} />
  else {
    const data = rule.data
    title = data.code
    // The version chosen, or the newest: the one a writer works on.
    const version = data.versions.find((item) => item.version === chosen) ?? data.versions.at(-1)
    body = (
      <div className="space-y-6">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>{data.title}</span>
          <Badge variant="outline">{t(`rules.kind.${data.kind}`)}</Badge>
          <TypologyTags typology={data.typology} />
        </div>
        <div className="grid gap-6 lg:grid-cols-[minmax(16rem,1fr)_2fr]">
          <div className="space-y-6">
            <VersionsCard rule={data} selected={version?.version ?? 0} onSelect={setChosen} />
            {version !== undefined && <VersionStateCard ruleId={data.id} version={version} />}
          </div>
          {version !== undefined && (
            <div className="space-y-6">
              {/*
                Keyed by the definition the server holds, so a save shows the server's values and
                sentence, while a status change alone (a simulation finishing) keeps unsaved edits.
              */}
              <VersionEditor
                key={`${version.id}:${version.definition_hash}`}
                ruleId={data.id}
                version={version}
                onDirtyChange={setUnsaved}
              />
              <SimulationPanel key={version.id} ruleId={data.id} version={version} unsaved={unsaved} />
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <>
      <PageHeader
        title={title}
        description={t('rules.detail.description')}
        actions={
          <Button asChild variant="outline">
            <Link to="/rules/monitoring?tab=tenant">
              <ArrowLeft aria-hidden />
              {t('rules.detail.back')}
            </Link>
          </Button>
        }
      />
      {body}
    </>
  )
}

/** One monitoring rule: its versions, the selected version's editor, simulation and lifecycle. */
export function RulePage() {
  const { ruleId } = useParams()
  if (ruleId === undefined || ruleId === '') return <NotFound />
  return <RuleDetailView key={ruleId} ruleId={ruleId} />
}
