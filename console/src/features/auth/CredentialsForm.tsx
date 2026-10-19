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
  tenantSlug: z.string().trim().min(1, msgKey('required')),
  email: z.string().trim().min(1, msgKey('required')).pipe(z.email(msgKey('email'))),
  password: z.string().min(1, msgKey('required')),
})
type Values = z.infer<typeof schema>

/** Step one of signing in. Hands over to `onMfa` when the account has TOTP enabled. */
export function CredentialsForm({ onMfa }: { onMfa: (mfaToken: string) => void }) {
  const { t } = useTranslation()
  const { login } = useAuth()
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { tenantSlug: '', email: '', password: '' },
  })

  const submit = handleSubmit(async (values) => {
    setFailure(null)
    try {
      const outcome = await login({
        tenantSlug: values.tenantSlug.toLowerCase(),
        email: values.email,
        password: values.password,
      })
      // Signed in: the page redirects as soon as the session is ready.
      if (outcome.kind === 'mfa') onMfa(outcome.mfaToken)
    } catch (error) {
      setFailure({ error })
    }
  })

  return (
    <form onSubmit={(event) => void submit(event)} noValidate className="space-y-4">
      {failure && (
        // Whatever the cause (workspace, email or password), a 401 reads the same, so the
        // form never reveals which of them was wrong.
        <FormAlert error={failure.error} whenStatus={{ status: 401, message: t('auth.login.invalid') }} />
      )}
      <Field
        label={t('auth.login.tenantSlug')}
        hint={t('auth.login.tenantSlugHint')}
        error={errors.tenantSlug?.message}
      >
        {(control) => (
          <Input
            {...control}
            {...register('tenantSlug')}
            autoComplete="organization"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            autoFocus
          />
        )}
      </Field>
      <Field label={t('auth.login.email')} error={errors.email?.message}>
        {(control) => (
          <Input
            {...control}
            {...register('email')}
            type="email"
            inputMode="email"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
          />
        )}
      </Field>
      <Field label={t('auth.login.password')} error={errors.password?.message}>
        {(control) => (
          <Input
            {...control}
            {...register('password')}
            type="password"
            autoComplete="current-password"
          />
        )}
      </Field>
      <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? t('auth.login.submitting') : t('auth.login.submit')}
      </Button>
    </form>
  )
}
