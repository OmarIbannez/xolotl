import { Download } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { FormDialog } from '@/features/parties/FormDialog'
import { useUploadTransactionFile, type UploadedFile } from './queries'
import { ACCEPT_ATTRIBUTE, checkUpload, contentTypeFor } from './upload'

/** The header-only template the console serves (`public/plantilla-transacciones.csv`). */
const TEMPLATE_HREF = '/plantilla-transacciones.csv'

/**
 * Upload a transactions file. The file is checked here (CSV, JSON Lines or XLSX, at most 50 MB)
 * before anything is sent; the API answers 202 for a new file and 200 for bytes it already has,
 * and either way the dialog hands the file's status to the card.
 */
export function UploadDialog({ onUploaded, onClose }: { onUploaded: (uploaded: UploadedFile) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const upload = useUploadTransactionFile()
  const [file, setFile] = useState<File | null>(null)
  const [problem, setProblem] = useState<string | undefined>(undefined)

  function problemOf(chosen: File): string | undefined {
    const verdict = checkUpload(chosen)
    return verdict === 'ok' ? undefined : t(`transactions.files.${verdict}`)
  }

  function choose(next: File | null) {
    setFile(next)
    setProblem(next === null ? undefined : problemOf(next))
    upload.reset()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (file === null) return setProblem('required')
    const refused = problemOf(file)
    const contentType = contentTypeFor(file)
    if (refused !== undefined || contentType === null) return setProblem(refused ?? t('transactions.files.unsupported'))
    upload.mutate(
      { file, contentType, name: file.name, size: file.size, lastModified: file.lastModified },
      {
        onSuccess: (uploaded) => {
          if (uploaded.created) toast.success(t('transactions.files.uploaded'))
          else toast.info(t('transactions.files.known'))
          onUploaded(uploaded)
          onClose()
        },
      },
    )
  }

  return (
    <FormDialog
      title={t('transactions.files.dialogTitle')}
      description={t('transactions.files.description')}
      pending={upload.isPending}
      failure={upload.isError ? { error: upload.error } : null}
      onSubmit={submit}
      onClose={onClose}
      submitLabel={t('transactions.files.submit')}
      pendingLabel={t('transactions.files.uploading')}
    >
      <Button asChild variant="outline" size="sm">
        <a href={TEMPLATE_HREF} download>
          <Download aria-hidden />
          {t('transactions.files.template')}
        </a>
      </Button>
      <Field label={t('transactions.files.file')} error={problem}>
        {(aria) => (
          <input
            {...aria}
            type="file"
            accept={ACCEPT_ATTRIBUTE}
            disabled={upload.isPending}
            onChange={(event) => choose(event.target.files?.[0] ?? null)}
            className="block w-full text-sm file:mr-3 file:rounded-md file:border file:bg-background file:px-3 file:py-1 file:text-sm"
          />
        )}
      </Field>
    </FormDialog>
  )
}
