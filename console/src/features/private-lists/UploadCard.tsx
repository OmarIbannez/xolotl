import { Download } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { hasProblemCode } from '@/api/errors'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { useUploadPrivateList } from './queries'
import { rowErrorsOf } from './row-errors'
import { RowErrorsTable } from './RowErrorsTable'
import { UPLOAD_ACCEPT, uploadContentType, uploadProblem } from './upload-file'

/** The header-only template the console serves (`public/plantilla-lista-privada.csv`). */
const TEMPLATE_HREF = '/plantilla-lista-privada.csv'

type Problem = ReturnType<typeof uploadProblem>

/**
 * Upload a new version of a private list. The file is checked here (CSV or XLSX, at most 20 MB)
 * before anything is sent; a file with bad rows comes back as a 422 whose row errors are listed
 * by row, column and code, never with a value.
 */
export function UploadCard({ listId }: { listId: string }) {
  const { t } = useTranslation()
  const upload = useUploadPrivateList(listId)
  const fileInput = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [problem, setProblem] = useState<Problem>(null)

  function problemText(found: Problem): string | undefined {
    if (found === null) return undefined
    return found === 'required' ? 'required' : t(`privateLists.upload.${found}`)
  }

  function choose(next: File | null) {
    setFile(next)
    setProblem(next === null ? null : uploadProblem(next))
    upload.reset()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (file === null) return setProblem('required')
    const refused = uploadProblem(file)
    const contentType = uploadContentType(file)
    if (refused !== null || contentType === null) return setProblem(refused ?? 'wrongType')
    upload.mutate(
      { file, contentType, name: file.name, size: file.size, lastModified: file.lastModified },
      {
        onSuccess: ({ data }) => {
          toast.success(t('privateLists.upload.uploaded', { seq: data.seq }))
          setFile(null)
          upload.reset()
          if (fileInput.current) fileInput.current.value = ''
        },
      },
    )
  }

  const invalid = hasProblemCode(upload.error, 'PRIVATE_LIST_INVALID') ? rowErrorsOf(upload.error) : null

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('privateLists.upload.title')}</CardTitle>
        <CardDescription>{t('privateLists.upload.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Button asChild variant="outline" size="sm">
          <a href={TEMPLATE_HREF} download>
            <Download aria-hidden />
            {t('privateLists.upload.template')}
          </a>
        </Button>
        <form onSubmit={submit} noValidate className="space-y-3">
          {upload.isError && <FormAlert error={upload.error} />}
          <div className="grid items-end gap-4 sm:grid-cols-[1fr_auto]">
            <Field label={t('privateLists.upload.file')} error={problemText(problem)}>
              {(aria) => (
                <input
                  {...aria}
                  ref={fileInput}
                  type="file"
                  accept={UPLOAD_ACCEPT}
                  disabled={upload.isPending}
                  onChange={(event) => choose(event.target.files?.[0] ?? null)}
                  className="block w-full text-sm file:mr-3 file:rounded-md file:border file:bg-background file:px-3 file:py-1 file:text-sm"
                />
              )}
            </Field>
            <Button type="submit" disabled={upload.isPending}>
              {upload.isPending ? t('privateLists.upload.uploading') : t('privateLists.upload.submit')}
            </Button>
          </div>
        </form>
        {invalid !== null && invalid.length > 0 && (
          <section className="space-y-2 border-t pt-4" aria-labelledby="row-errors-title">
            <h3 id="row-errors-title" className="text-sm font-medium">
              {t('privateLists.rowErrors.title')}
            </h3>
            <p className="text-sm text-muted-foreground">{t('privateLists.rowErrors.description')}</p>
            <RowErrorsTable errors={invalid} />
          </section>
        )}
      </CardContent>
    </Card>
  )
}
