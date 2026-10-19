import { zodResolver } from '@hookform/resolvers/zod'
import { TriangleAlert } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { useEffect, useId, useRef } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'
import { isApiError } from '@/api/errors'
import { CopyButton } from '@/components/common/CopyButton'
import { Field } from '@/components/common/Field'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormAlert } from '@/features/auth/FormAlert'
import { msgKey } from '@/i18n/validation'
import { groupSecret, QR } from './qr'
import { useConfirmTotp } from './queries'

const schema = z.object({
  code: z.string().trim().regex(/^\d{6,8}$/, msgKey('totpCode')),
})
type Values = z.infer<typeof schema>

interface EnrolPanelProps {
  /** The base32 secret, shown for typing into an app that cannot scan. Shown once. */
  secret: string
  /** The `otpauth://` address the QR code carries. */
  uri: string
  /** The code was accepted: TOTP is on. */
  onActivated: () => void
  /** The API says TOTP is already on (another tab or device got there first). */
  onAlreadyEnabled: () => void
  onCancel: () => void
}

/**
 * The two steps of turning TOTP on: scan the QR code (or type the secret) into an
 * authenticator app, then prove it works with a code from the app. The secret exists only
 * in the props of this panel; nothing here writes it anywhere.
 */
export function EnrolPanel({ secret, uri, onActivated, onAlreadyEnabled, onCancel }: EnrolPanelProps) {
  const { t } = useTranslation()
  const scanId = useId()
  const manualId = useId()
  const confirmId = useId()
  const confirm = useConfirmTotp()
  const scanHeading = useRef<HTMLHeadingElement>(null)
  const {
    register,
    handleSubmit,
    resetField,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { code: '' } })

  // The button that opened this panel is gone: start the keyboard and screen reader here.
  useEffect(() => {
    scanHeading.current?.focus()
  }, [])

  const status = isApiError(confirm.error) ? confirm.error.status : undefined
  const wrongCode = status === 422
  const showFailure = confirm.isError && status !== 422 && status !== 409

  const submit = handleSubmit(async ({ code }) => {
    try {
      await confirm.mutateAsync(code)
    } catch (error) {
      if (isApiError(error) && error.status === 409) onAlreadyEnabled()
      else if (isApiError(error) && error.status === 422) {
        // A wrong code is the ordinary mistake: start the field over, ready for the next one.
        resetField('code')
        setFocus('code')
      }
      return // whatever else went wrong is read from `confirm.error` below
    }
    onActivated()
  })

  return (
    <div className="space-y-5">
      <Alert className="border-warning/40 bg-warning/10">
        <TriangleAlert aria-hidden className="text-warning" />
        <AlertTitle>{t('security.totp.onceTitle')}</AlertTitle>
        <AlertDescription>{t('security.totp.onceBody')}</AlertDescription>
      </Alert>

      <div className="grid gap-6 sm:grid-cols-[auto_1fr]">
        <section aria-labelledby={scanId} className="space-y-3">
          <h3 id={scanId} ref={scanHeading} tabIndex={-1} className="text-sm font-medium outline-none">
            {t('security.totp.scanTitle')}
          </h3>
          <p className="max-w-56 text-sm text-muted-foreground">{t('security.totp.scanBody')}</p>
          <QRCodeSVG
            value={uri}
            {...QR}
            title={t('security.totp.qrLabel')}
            aria-label={t('security.totp.qrLabel')}
            className="rounded-lg ring-1 ring-border"
          />
        </section>

        <div className="min-w-0 space-y-5">
          <section aria-labelledby={manualId} className="space-y-2">
            <h3 id={manualId} className="text-sm font-medium">
              {t('security.totp.manualTitle')}
            </h3>
            <p className="text-sm text-muted-foreground">{t('security.totp.manualBody')}</p>
            <p className="text-xs text-muted-foreground">{t('security.totp.secret')}</p>
            <div className="flex items-center gap-2">
              <code className="rounded-md border bg-surface-2 px-2 py-1 font-mono text-sm tracking-wide break-words select-all">
                {groupSecret(secret)}
              </code>
              <CopyButton
                value={secret}
                label={t('security.totp.copySecret')}
                copiedMessage={t('security.totp.secretCopied')}
                failedMessage={t('security.totp.copyFailed')}
              />
            </div>
          </section>

          <form
            aria-labelledby={confirmId}
            onSubmit={(event) => void submit(event)}
            noValidate
            className="space-y-3"
          >
            <h3 id={confirmId} className="text-sm font-medium">
              {t('security.totp.confirmTitle')}
            </h3>
            {showFailure && <FormAlert error={confirm.error} />}
            <Field
              label={t('security.totp.code')}
              hint={t('security.totp.codeHint')}
              error={errors.code?.message ?? (wrongCode ? t('security.totp.codeInvalid') : undefined)}
            >
              {(control) => (
                <Input
                  {...control}
                  {...register('code')}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={8}
                  className="max-w-48 font-mono tracking-widest"
                />
              )}
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? t('security.totp.confirming') : t('security.totp.confirm')}
              </Button>
              <Button type="button" variant="ghost" disabled={isSubmitting} onClick={onCancel}>
                {t('security.totp.cancel')}
              </Button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
