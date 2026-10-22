import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { hasProblemCode } from '@/api/errors'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { Field } from '@/components/common/Field'
import { KeyValues } from '@/components/common/page'
import { ErrorBlock, LoadingBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useFormat } from '@/i18n/use-locale'
import { useTransactionBatch } from './queries'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Look a batch received by API up by its id: its source and counts. */
export function BatchesCard() {
  const { t } = useTranslation()
  const format = useFormat()
  const [draft, setDraft] = useState('')
  const [problem, setProblem] = useState<string | undefined>(undefined)
  const [batchId, setBatchId] = useState<string | undefined>(undefined)
  const batch = useTransactionBatch(batchId)

  function lookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const id = draft.trim()
    if (id === '') return setProblem('required')
    if (!UUID.test(id)) return setProblem('uuid')
    setProblem(undefined)
    setBatchId(id.toLowerCase())
  }

  let result = null
  if (batchId !== undefined) {
    if (batch.isError) {
      result = hasProblemCode(batch.error, 'NOT_FOUND') ? (
        <p className="text-sm text-muted-foreground">{t('transactions.batches.notFound')}</p>
      ) : (
        <ErrorBlock error={batch.error} onRetry={() => void batch.refetch()} />
      )
    } else if (batch.data === undefined) result = <LoadingBlock rows={3} />
    else {
      const found = batch.data
      result = (
        <KeyValues
          items={[
            [t('transactions.batches.source'), t(`transactions.batches.sourceKind.${found.source}`)],
            [t('transactions.batches.itemCount'), format.number(found.item_count)],
            [t('transactions.batches.accepted'), format.number(found.accepted)],
            [t('transactions.batches.duplicates'), format.number(found.duplicates)],
            [t('transactions.batches.receivedAt'), <DateTimeCell key="received" value={found.received_at} />],
            [
              t('transactions.batches.requestId'),
              <span key="request" className="font-mono text-xs">
                {found.request_id ?? '—'}
              </span>,
            ],
          ]}
        />
      )
    }
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('transactions.batches.title')}</CardTitle>
        <CardDescription>{t('transactions.batches.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={lookup} noValidate className="grid items-end gap-3 sm:grid-cols-[1fr_auto]">
          <Field label={t('transactions.batches.batchId')} error={problem}>
            {(aria) => (
              <Input
                {...aria}
                className="font-mono"
                autoComplete="off"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
              />
            )}
          </Field>
          <Button type="submit" variant="outline">
            {t('transactions.batches.lookup')}
          </Button>
        </form>
        {result}
      </CardContent>
    </Card>
  )
}
