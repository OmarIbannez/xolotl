import { zodResolver } from '@hookform/resolvers/zod'
import { useId, useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { hasProblemCode } from '@/api/errors'
import { fieldProblems } from '@/api/field-errors'
import { useCreateApiKey, useTenant } from '@/api/queries'
import type { ApiKeyCreatedResponse } from '@/api/types'
import { EnvBadge } from '@/components/common/badges'
import { Field } from '@/components/common/Field'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { FormAlert } from '@/features/auth/FormAlert'
import { apiKeySchema, toApiKeyRequest, type ApiKeyFormValues } from './form'
import { ScopesField } from './ScopesField'

interface CreateApiKeyDialogProps {
  onClose: () => void
  /** Called once with the created key, whose `key` the caller must show and then drop. */
  onCreated: (created: ApiKeyCreatedResponse) => void
}

/**
 * The "new API key" form. Open for as long as it is rendered, so the form and the
 * submission's idempotency state start afresh each time the caller shows it.
 *
 * Pressing submit sends one request (one `Idempotency-Key`); pressing it again after a
 * failure that left the outcome unknown resends it with the same key, so the API cannot
 * create the key twice. The dialog cannot be dismissed while the request is out, so its
 * answer, which holds the one-time secret, always reaches `onCreated`.
 */
export function CreateApiKeyDialog({ onClose, onCreated }: CreateApiKeyDialogProps) {
  const { t } = useTranslation()
  const tenant = useTenant()
  const create = useCreateApiKey()
  const [failure, setFailure] = useState<{ error: unknown } | null>(null)
  const envLabelId = useId()
  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<ApiKeyFormValues>({
    resolver: zodResolver(apiKeySchema),
    defaultValues: { name: '', scopes: [], scopeDraft: '' },
  })
  const env = tenant.data?.env

  const submit = handleSubmit(async (values) => {
    if (env === undefined) return
    setFailure(null)
    try {
      const { data } = await create.mutateAsync(toApiKeyRequest(values, env))
      onCreated(data)
    } catch (error) {
      const problems = fieldProblems(error, ['name', 'scopes'] as const)
      for (const { field, message } of problems) setError(field, { type: 'server', message })
      if (problems.length === 0) setFailure({ error })
    }
  })

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !isSubmitting) onClose()
      }}
    >
      <DialogContent showCloseButton={!isSubmitting} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('apiKeys.create.title')}</DialogTitle>
          <DialogDescription>{t('apiKeys.create.description')}</DialogDescription>
        </DialogHeader>
        <form onSubmit={(event) => void submit(event)} noValidate className="space-y-4">
          {failure && (
            <FormAlert
              error={failure.error}
              whenStatus={
                hasProblemCode(failure.error, 'CONFLICT')
                  ? { status: 409, message: t('apiKeys.create.conflict') }
                  : undefined
              }
            />
          )}
          <Field
            label={t('apiKeys.create.name')}
            hint={t('apiKeys.create.nameHint')}
            error={errors.name?.message}
          >
            {(aria) => <Input {...aria} {...register('name')} autoComplete="off" autoFocus />}
          </Field>
          <ScopesField control={control} />
          <div role="group" aria-labelledby={envLabelId} className="space-y-1.5">
            <p id={envLabelId} className="text-sm leading-none font-medium">
              {t('apiKeys.create.env')}
            </p>
            {env ? (
              <EnvBadge env={env} />
            ) : tenant.isError ? (
              <ErrorBlock error={tenant.error} />
            ) : (
              <Skeleton className="h-5 w-24" />
            )}
            <p className="text-xs text-muted-foreground">{t('apiKeys.create.envHint')}</p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={isSubmitting || env === undefined}>
              {isSubmitting ? t('apiKeys.create.submitting') : t('apiKeys.create.submit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
