import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { useSetLegalHold } from './queries'

interface LegalHoldCardProps {
  partyId: string
  /** Whether the party is on legal hold now. */
  legalHold: boolean
}

/**
 * Place a party on legal hold or release it (MLRO only; the caller renders this card for that
 * role alone). A party on hold is never purged by the retention sweep. Asks before either change.
 */
export function LegalHoldCard({ partyId, legalHold }: LegalHoldCardProps) {
  const { t } = useTranslation()
  const setHold = useSetLegalHold(partyId)
  const [asking, setAsking] = useState(false)
  const next = !legalHold
  const action = next ? t('parties.legalHold.set') : t('parties.legalHold.clear')

  function cancel() {
    setHold.reset()
    setAsking(false)
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle>{t('parties.legalHold.title')}</CardTitle>
        <CardAction>
          <Button type="button" variant="outline" size="sm" onClick={() => setAsking(true)}>
            {action}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        {legalHold ? t('parties.detail.legalHoldOn') : t('parties.detail.legalHoldOff')}
      </CardContent>
      {asking && (
        <ConfirmDialog
          title={action}
          description={next ? t('parties.legalHold.confirmSet') : t('parties.legalHold.confirmClear')}
          confirmLabel={action}
          pendingLabel={t('parties.legalHold.pending')}
          pending={setHold.isPending}
          alert={setHold.isError ? <FormAlert error={setHold.error} /> : undefined}
          onConfirm={() =>
            setHold.mutate({ legal_hold: next }, { onSuccess: () => setAsking(false) })
          }
          onCancel={cancel}
        />
      )}
    </Card>
  )
}
