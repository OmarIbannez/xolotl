import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { ListKind, PrivateListCreate } from '@/api/types'
import { Field } from '@/components/common/Field'
import { Input } from '@/components/ui/input'
import { FormDialog, SelectField } from '@/features/parties/FormDialog'
import { useCreatePrivateList } from './queries'

const LPB_CODE = 'MX_LPB'
const INTERNAL_PREFIX = 'INTERNAL_'
/** The API's rule for the part after `INTERNAL_` (`^INTERNAL_[A-Z0-9_]{1,40}$`). */
const SLUG = /^[A-Z0-9_]{1,40}$/
const MAX_TITLE = 200
const INTERNAL_KINDS = ['INTERNAL', 'SANCTION', 'PEP'] as const satisfies readonly ListKind[]
type InternalKind = (typeof INTERNAL_KINDS)[number]
type ListType = 'LPB' | 'INTERNAL'

interface Problems {
  slug?: string
  title?: string
}

/**
 * Create the tenant's LPB (always kind `BLOCKED`, so no kind is sent) or an internal list
 * `INTERNAL_<SLUG>` of kind `INTERNAL`, `SANCTION` or `PEP`. The API refuses a second LPB with
 * 409 `PRIVATE_LIST_EXISTS`, shown in the dialog.
 */
export function CreatePrivateListDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const create = useCreatePrivateList()
  const [type, setType] = useState<ListType>('LPB')
  const [slug, setSlug] = useState('')
  const [kind, setKind] = useState<InternalKind>('INTERNAL')
  const [title, setTitle] = useState('')
  const [problems, setProblems] = useState<Problems>({})

  function check(): Problems {
    const found: Problems = {}
    if (type === 'INTERNAL' && !SLUG.test(slug)) found.slug = t('privateLists.create.slugInvalid')
    if (title.trim() === '') found.title = 'required'
    else if (title.trim().length > MAX_TITLE) found.title = t('privateLists.create.titleTooLong')
    return found
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const found = check()
    setProblems(found)
    if (Object.keys(found).length > 0) return
    const body: PrivateListCreate =
      type === 'LPB'
        ? { list_code: LPB_CODE, title: title.trim() }
        : { list_code: `${INTERNAL_PREFIX}${slug}`, title: title.trim(), list_kind: kind }
    create.mutate(body, {
      onSuccess: ({ data }) => {
        toast.success(t('privateLists.create.created', { code: data.list_code }))
        onClose()
      },
    })
  }

  const radio = (value: ListType, label: string) => (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="radio"
        name="private-list-type"
        value={value}
        checked={type === value}
        disabled={create.isPending}
        onChange={() => setType(value)}
        className="size-4 accent-primary"
      />
      {label}
    </label>
  )

  return (
    <FormDialog
      title={t('privateLists.create.title')}
      description={t('privateLists.create.description')}
      pending={create.isPending}
      failure={create.isError ? { error: create.error } : null}
      onSubmit={submit}
      onClose={onClose}
      submitLabel={t('privateLists.create.submit')}
      pendingLabel={t('privateLists.create.creating')}
    >
      <fieldset className="space-y-2">
        <legend className="mb-1.5 text-sm font-medium">{t('privateLists.create.type')}</legend>
        {radio('LPB', t('privateLists.create.typeLpb'))}
        {radio('INTERNAL', t('privateLists.create.typeInternal'))}
      </fieldset>
      {type === 'INTERNAL' && (
        <>
          <Field label={t('privateLists.create.slug')} hint={t('privateLists.create.slugHint')} error={problems.slug}>
            {(aria) => (
              <Input
                {...aria}
                value={slug}
                maxLength={40}
                autoComplete="off"
                className="font-mono uppercase"
                disabled={create.isPending}
                onChange={(event) => setSlug(event.target.value.toUpperCase())}
              />
            )}
          </Field>
          <SelectField
            label={t('privateLists.create.kind')}
            value={kind}
            options={INTERNAL_KINDS}
            optionLabel={(value) => t(`privateLists.kind.${value}`)}
            onChange={setKind}
            disabled={create.isPending}
          />
        </>
      )}
      <Field label={t('privateLists.create.listTitle')} error={problems.title}>
        {(aria) => (
          <Input
            {...aria}
            value={title}
            autoComplete="off"
            disabled={create.isPending}
            onChange={(event) => setTitle(event.target.value)}
          />
        )}
      </Field>
    </FormDialog>
  )
}
