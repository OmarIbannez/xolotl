import { ShieldCheck, ShieldOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { isApiError } from '@/api/errors'
import { useAuth, useUser } from '@/auth/use-auth'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { EnrolPanel } from './EnrolPanel'
import { useEnrollTotp } from './queries'

/**
 * Two-step verification: whether it is on and, when it is not, the way to turn it on
 * (enrol, scan, confirm). There is no way to turn it off from here in this increment.
 */
export function TotpCard() {
  const { t } = useTranslation()
  const user = useUser()
  const { refreshMe } = useAuth()
  const enroll = useEnrollTotp()
  const [justEnabled, setJustEnabled] = useState(false)
  const [alreadyEnabled, setAlreadyEnabled] = useState(false)
  const success = useRef<HTMLDivElement>(null)
  const notice = useRef<HTMLDivElement>(null)
  const enableButton = useRef<HTMLButtonElement>(null)
  const refocusEnable = useRef(false)

  // The panel that held the focus is gone: let the confirmation be what is read next.
  useEffect(() => {
    if (justEnabled) success.current?.focus()
  }, [justEnabled])

  // Likewise when TOTP turns out to be on already: the notice says what happened, and it
  // stays put while the profile refreshes under it (the Activar button is about to go).
  useEffect(() => {
    if (alreadyEnabled) notice.current?.focus()
  }, [alreadyEnabled])

  // Cancelled: the panel is gone and the Activar button is back, so hand the focus to it
  // (the control that held it no longer exists). Runs after every render, and only acts once.
  useEffect(() => {
    if (!refocusEnable.current) return
    refocusEnable.current = false
    enableButton.current?.focus()
  })

  const enabled = user.totp_enabled || justEnabled
  // Bring the profile up to date; the badge follows `totp_enabled` from it.
  const resync = () => void refreshMe().catch(() => undefined)

  // TOTP can come on some other way while a secret is on screen (another tab, say). Once it
  // is on there is nothing left to enrol, so the secret must not linger in memory.
  const { reset: forgetEnrolment, isSuccess: enrolling } = enroll
  useEffect(() => {
    if (enabled && enrolling) forgetEnrolment()
  }, [enabled, enrolling, forgetEnrolment])

  function begin() {
    setAlreadyEnabled(false)
    enroll.mutate(undefined, {
      onError: (error) => {
        if (!isApiError(error) || error.status !== 409) return
        // Another tab or device turned it on after this page loaded.
        setAlreadyEnabled(true)
        resync()
      },
    })
  }

  function activated() {
    enroll.reset() // the secret goes with the enrolment
    setJustEnabled(true)
    toast.success(t('security.totp.success'))
    resync()
  }

  function foundAlreadyEnabled() {
    enroll.reset()
    setAlreadyEnabled(true)
    resync()
  }

  function cancel() {
    refocusEnable.current = true
    enroll.reset()
  }

  return (
    <Card className="animate-fade-in-up">
      <CardHeader>
        <CardTitle role="heading" aria-level={2}>
          {t('security.totp.title')}
        </CardTitle>
        <CardDescription>{t('security.totp.description')}</CardDescription>
        <CardAction>
          {enabled ? (
            <Badge variant="good">
              <ShieldCheck aria-hidden />
              {t('security.totp.on')}
            </Badge>
          ) : (
            <Badge variant="muted">
              <ShieldOff aria-hidden />
              {t('security.totp.off')}
            </Badge>
          )}
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        {alreadyEnabled && (
          <Alert ref={notice} tabIndex={-1} className="outline-none">
            <AlertDescription>{t('security.totp.alreadyEnabled')}</AlertDescription>
          </Alert>
        )}

        {enabled && (
          <div ref={success} tabIndex={-1} role="status" className="space-y-1 text-sm outline-none">
            {justEnabled && <p className="font-medium">{t('security.totp.success')}</p>}
            <p className={justEnabled ? 'text-muted-foreground' : undefined}>
              {justEnabled ? t('security.totp.successBody') : t('security.totp.onBody')}
            </p>
          </div>
        )}

        {!enabled && enroll.isSuccess && (
          <EnrolPanel
            secret={enroll.data.secret}
            uri={enroll.data.otpauth_uri}
            onActivated={activated}
            onAlreadyEnabled={foundAlreadyEnabled}
            onCancel={cancel}
          />
        )}

        {!enabled && !enroll.isSuccess && (
          <div className="space-y-3">
            <p className="text-sm">{t('security.totp.offBody')}</p>
            {enroll.isError && !alreadyEnabled && <FormAlert error={enroll.error} />}
            <Button ref={enableButton} type="button" onClick={begin} disabled={enroll.isPending}>
              {enroll.isPending ? t('security.totp.enabling') : t('security.totp.enable')}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
