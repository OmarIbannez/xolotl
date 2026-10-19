import { zodResolver } from '@hookform/resolvers/zod'
import { Ban, CircleCheck, CircleDashed, CircleOff, CircleSlash, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { z } from 'zod'
import type { CustomerRelationshipResponse, CustomerRelationshipStatus } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { Field } from '@/components/common/Field'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { FormAlert } from '@/features/auth/FormAlert'
import { useCloseRelationship, useOpenRelationship } from './queries'
import {
  CUSTOMER_FIELD_OF,
  customerSchema,
  showFieldProblems,
  toRelationshipOpenRequest,
  type CustomerFormValues,
} from './sub-forms'

const STATUS: Record<
  CustomerRelationshipStatus,
  { variant: 'good' | 'info' | 'warning' | 'critical' | 'muted'; icon: LucideIcon }
> = {
  PENDING: { variant: 'info', icon: CircleDashed },
  ACTIVE: { variant: 'good', icon: CircleCheck },
  RESTRICTED: { variant: 'warning', icon: CircleSlash },
  BLOCKED: { variant: 'critical', icon: Ban },
  EXITED: { variant: 'muted', icon: CircleOff },
}

/** A customer relationship's status, with an icon and a label (never color alone). */
function StatusBadge({ status }: { status: CustomerRelationshipStatus }) {
  const { t } = useTranslation()
  const { variant, icon: Icon } = STATUS[status]
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(`parties.customer.status.${status}`)}
    </Badge>
  )
}

/** Open a customer relationship for this party. */
function OpenForm({ partyId }: { partyId: string }) {
  const { t } = useTranslation()
  const open = useOpenRelationship()
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<CustomerFormValues, unknown, z.output<typeof customerSchema>>({
    resolver: zodResolver(customerSchema),
    defaultValues: { partyId, externalRef: '', product: '', accountTier: '' },
  })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      await open.mutateAsync(toRelationshipOpenRequest(values))
      toast.success(t('parties.customer.opened'))
      reset()
    } catch (error) {
      if (!showFieldProblems(error, CUSTOMER_FIELD_OF, setError)) setFailure({ error })
    }
  })

  return (
    <form onSubmit={(event) => void submit(event)} noValidate className="space-y-3 border-t pt-4">
      {failure && <FormAlert error={failure.error} />}
      <div className="grid items-start gap-4 sm:grid-cols-[1fr_10rem_10rem_auto]">
        <Field label={t('parties.customer.externalRef')} error={errors.externalRef?.message}>
          {(aria) => <Input {...aria} {...register('externalRef')} autoComplete="off" className="font-mono" />}
        </Field>
        <Field label={t('parties.customer.product')} error={errors.product?.message}>
          {(aria) => <Input {...aria} {...register('product')} autoComplete="off" />}
        </Field>
        <Field label={t('parties.customer.tier')} error={errors.accountTier?.message}>
          {(aria) => <Input {...aria} {...register('accountTier')} autoComplete="off" />}
        </Field>
        <Button type="submit" disabled={isSubmitting} className="sm:mt-6">
          {isSubmitting ? t('parties.customer.opening') : t('parties.customer.open')}
        </Button>
      </div>
    </form>
  )
}

interface CustomerRelationshipsCardProps {
  partyId: string
  relationships: readonly CustomerRelationshipResponse[]
}

/**
 * A party's customer relationships: their status, the form that opens one and the close that
 * starts the party's retention clock (asked first: it cannot be undone).
 */
export function CustomerRelationshipsCard({ partyId, relationships }: CustomerRelationshipsCardProps) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const canWrite = hasRole(ACCESS.partiesWrite)
  const close = useCloseRelationship()
  const [closing, setClosing] = useState<CustomerRelationshipResponse | null>(null)

  function cancel() {
    close.reset()
    setClosing(null)
  }

  const columns: Column<CustomerRelationshipResponse>[] = [
    { id: 'externalRef', header: t('parties.customer.externalRef'), cell: (row) => <span className="font-mono">{row.external_ref}</span> },
    { id: 'product', header: t('parties.customer.product'), cell: (row) => row.product ?? '—' },
    { id: 'tier', header: t('parties.customer.tier'), cell: (row) => row.account_tier ?? '—' },
    { id: 'status', header: t('parties.customer.statusLabel'), cell: (row) => <StatusBadge status={row.status} /> },
    {
      id: 'startedAt',
      header: t('parties.customer.startedAt'),
      cell: (row) => (row.started_at === null ? '—' : <DateTimeCell value={row.started_at} />),
    },
    {
      id: 'endedAt',
      header: t('parties.customer.endedAt'),
      cell: (row) => (row.ended_at === null ? '—' : <DateTimeCell value={row.ended_at} />),
    },
  ]
  if (canWrite) {
    columns.push({
      id: 'close',
      header: <span className="sr-only">{t('parties.customer.close')}</span>,
      cell: (row) =>
        row.status === 'EXITED' ? null : (
          <Button type="button" variant="outline" size="sm" onClick={() => setClosing(row)}>
            {t('parties.customer.close')}
          </Button>
        ),
      className: 'text-right',
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('parties.customer.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <DataTable
          label={t('parties.customer.title')}
          columns={columns}
          rows={relationships}
          getRowId={(row) => row.id}
          empty={t('parties.customer.empty')}
        />
        {canWrite && <OpenForm partyId={partyId} />}
      </CardContent>
      {closing && (
        <ConfirmDialog
          title={t('parties.customer.close')}
          description={t('parties.customer.confirmClose')}
          confirmLabel={t('parties.customer.close')}
          pendingLabel={t('parties.customer.closing')}
          pending={close.isPending}
          alert={close.isError ? <FormAlert error={close.error} /> : undefined}
          onConfirm={() =>
            close.mutate(
              { relationshipId: closing.id },
              {
                onSuccess: () => {
                  toast.success(t('parties.customer.closed'))
                  setClosing(null)
                },
              },
            )
          }
          onCancel={cancel}
        />
      )}
    </Card>
  )
}
