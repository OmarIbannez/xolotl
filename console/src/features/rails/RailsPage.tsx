import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { FailMode, Rail, RailSettingResponse } from '@/api/types'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { FormAlert } from '@/features/auth/FormAlert'
import { useRails, useSetRailMode } from './queries'

/** The change the ADMIN is being asked to confirm. */
interface Pending {
  rail: Rail
  failMode: FailMode
}

/**
 * Admin › Rieles: what the decide path answers on each rail when the engine cannot evaluate an
 * operation in time (`FAIL_OPEN` answers REVIEW, `FAIL_CLOSED` answers BLOCK). A rail without a
 * setting follows the deployment's default. Each change is asked first, since it decides whether
 * unevaluated operations go through, and the API audits it.
 */
export function RailsPage() {
  const { t } = useTranslation()
  const rails = useRails()
  const setMode = useSetRailMode()
  const [pending, setPending] = useState<Pending | null>(null)

  const railName = (rail: Rail) => t(`transactions.rail.${rail}`)

  function cancel() {
    setMode.reset()
    setPending(null)
  }

  function confirm(change: Pending) {
    setMode.mutate(change, {
      onSuccess: () => {
        toast.success(t('rails.changed', { rail: railName(change.rail), mode: t(`rails.mode.${change.failMode}`) }))
        setPending(null)
      },
    })
  }

  const columns: Column<RailSettingResponse>[] = [
    {
      id: 'rail',
      header: t('rails.table.rail'),
      cell: (row) => (
        <span className="flex flex-col">
          <span>{railName(row.rail)}</span>
          <span className="font-mono text-xs text-muted-foreground">{row.rail}</span>
        </span>
      ),
    },
    { id: 'mode', header: t('rails.table.mode'), cell: (row) => t(`rails.mode.${row.fail_mode}`) },
    {
      id: 'source',
      header: t('rails.table.source'),
      cell: (row) =>
        row.explicit ? <Badge variant="info">{t('rails.explicit')}</Badge> : <Badge variant="muted">{t('rails.default')}</Badge>,
    },
    {
      id: 'updatedAt',
      header: t('rails.table.updatedAt'),
      cell: (row) => (row.updated_at === null ? '—' : <DateTimeCell value={row.updated_at} />),
    },
    {
      id: 'restrictive',
      header: t('rails.table.restrictive'),
      cell: (row) => (
        <Switch
          checked={row.fail_mode === 'FAIL_CLOSED'}
          aria-label={t('rails.toggle', { rail: railName(row.rail) })}
          disabled={setMode.isPending}
          onCheckedChange={(closed) => setPending({ rail: row.rail, failMode: closed ? 'FAIL_CLOSED' : 'FAIL_OPEN' })}
        />
      ),
    },
  ]

  return (
    <>
      <PageHeader title={t('rails.title')} description={t('rails.description')} />

      <Card className="animate-fade-in-up">
        <CardContent className="space-y-4">
          <p className="max-w-3xl text-sm text-muted-foreground">{t('rails.explainer')}</p>
          {rails.isError ? (
            <ErrorBlock error={rails.error} onRetry={() => void rails.refetch()} />
          ) : (
            <DataTable
              label={t('rails.table.label')}
              columns={columns}
              rows={rails.data}
              getRowId={(row) => row.rail}
              loading={rails.isPending}
              empty={t('rails.empty')}
            />
          )}
        </CardContent>
      </Card>

      {pending && (
        <ConfirmDialog
          title={t(`rails.confirm.${pending.failMode}.title`, { rail: railName(pending.rail) })}
          description={t(`rails.confirm.${pending.failMode}.description`, { rail: railName(pending.rail) })}
          confirmLabel={t('rails.confirm.submit')}
          pendingLabel={t('rails.confirm.pending')}
          pending={setMode.isPending}
          alert={setMode.isError ? <FormAlert error={setMode.error} /> : undefined}
          onConfirm={() => confirm(pending)}
          onCancel={cancel}
        />
      )}
    </>
  )
}
