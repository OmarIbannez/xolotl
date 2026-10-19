import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'
import { useAuth } from '@/auth/use-auth'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { msgKey } from '@/i18n/validation'
import { FormAlert } from './FormAlert'

const schema = z.object({
  code: z.string().trim().regex(/^\d{6,8}$/, msgKey('totpCode')),
})
type Values = z.infer<typeof schema>

/** Step two of signing in: the 6 to 8 digit code. The MFA token lives only in memory. */
export function TotpForm({ mfaToken }: { mfaToken: string }) {
  const { t } = useTranslation()
  const { loginTotp } = useAuth()
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { code: '' } })

  const submit = handleSubmit(async ({ code }) => {
    setFailure(null)
    try {
      await loginTotp({ mfaToken, code })
      // Signed in: the page redirects as soon as the session is ready.
    } catch (error) {
      setFailure({ error })
    }
  })

  return (
    <form onSubmit={(event) => void submit(event)} noValidate className="space-y-4">
      {failure && (
        // A wrong code and an expired verification (it lasts 5 minutes) both answer 401.
        <FormAlert error={failure.error} whenStatus={{ status: 401, message: t('auth.totp.invalid') }} />
      )}
      <Field label={t('auth.totp.code')} error={errors.code?.message}>
        {(control) => (
          <Input
            {...control}
            {...register('code')}
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={8}
            className="font-mono tracking-widest"
            autoFocus
          />
        )}
      </Field>
      <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? t('auth.totp.submitting') : t('auth.totp.submit')}
      </Button>
    </form>
  )
}
