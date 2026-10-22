import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { BackfillRequest } from '@/api/types'
import { Field } from '@/components/common/Field'
import { Input } from '@/components/ui/input'
import { FormDialog } from '@/features/parties/FormDialog'
import { mexicoCityToday } from './dates'
import { useRequestBackfill } from './queries'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

interface Problems {
  from?: string
  to?: string
  partyId?: string
}

/**
 * "Ejecutar backfill": the range of operation dates (Mexico City days, both included) and,
 * optionally, one party. The API checks the range again (`BACKFILL_RANGE_INVALID`).
 */
export function BackfillDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const request = useRequestBackfill()
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [partyId, setPartyId] = useState('')
  const [problems, setProblems] = useState<Problems>({})
  const [today] = useState(mexicoCityToday)

  function check(): Problems {
    const found: Problems = {}
    if (from === '') found.from = 'required'
    if (to === '') found.to = 'required'
    else if (from !== '' && to < from) found.to = t('transactions.backfill.rangeOrder')
    if (partyId.trim() !== '' && !UUID.test(partyId.trim())) found.partyId = 'uuid'
    return found
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const found = check()
    setProblems(found)
    if (Object.keys(found).length > 0) return
    const party = partyId.trim()
    const body: BackfillRequest = party === '' ? { from, to } : { from, to, party_id: party }
    request.mutate(body, {
      onSuccess: () => {
        toast.success(t('transactions.backfill.requested'))
        onClose()
      },
    })
  }

  return (
    <FormDialog
      title={t('transactions.backfill.run')}
      description={t('transactions.backfill.dialogDescription')}
      pending={request.isPending}
      failure={request.isError ? { error: request.error } : null}
      onSubmit={submit}
      onClose={onClose}
      submitLabel={t('transactions.backfill.submit')}
      pendingLabel={t('transactions.backfill.running')}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t('transactions.backfill.from')} error={problems.from}>
          {(aria) => <Input {...aria} type="date" max={today} value={from} onChange={(event) => setFrom(event.target.value)} />}
        </Field>
        <Field label={t('transactions.backfill.to')} error={problems.to}>
          {(aria) => <Input {...aria} type="date" max={today} value={to} onChange={(event) => setTo(event.target.value)} />}
        </Field>
      </div>
      <Field label={t('transactions.backfill.partyId')} error={problems.partyId}>
        {(aria) => (
          <Input
            {...aria}
            className="font-mono"
            autoComplete="off"
            value={partyId}
            onChange={(event) => setPartyId(event.target.value)}
          />
        )}
      </Field>
    </FormDialog>
  )
}
