import { CircleAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { isApiError } from '@/api/errors'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { describeError } from '@/i18n/errors'

interface FormAlertProps {
  /** Whatever the API layer threw. */
  error: unknown
  /** What to say, instead of the generic mapping, when the failure has this HTTP status. */
  whenStatus?: { status: number; message: string }
}

/**
 * A form-level failure ("wrong credentials", "server unreachable"), announced to screen
 * readers. The message is worked out when it renders, so it follows the language switcher.
 */
export function FormAlert({ error, whenStatus }: FormAlertProps) {
  const { t } = useTranslation()
  const described = describeError(t, error)
  const specific = whenStatus && isApiError(error) && error.status === whenStatus.status
  const message = specific ? whenStatus.message : described.message
  // A specific message deliberately says little (and a generic one has nothing to quote).
  const reference = specific ? undefined : described.reference
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden />
      <AlertDescription>
        {message}
        {reference && (
          <span className="mt-0.5 block font-mono text-xs">{t('errors.reference', { id: reference })}</span>
        )}
      </AlertDescription>
    </Alert>
  )
}
