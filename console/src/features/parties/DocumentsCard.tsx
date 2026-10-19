import { Download } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { DocumentResponse, DocumentType } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { useFormat } from '@/i18n/use-locale'
import type { ValidationKey } from '@/i18n/validation'
import { SelectField } from './FormDialog'
import { documentFilename, downloadDocument, useUploadDocument } from './queries'
import { ALLOWED_DOCUMENT_TYPES, DOCUMENT_TYPES, documentProblem } from './sub-forms'

interface DocumentsCardProps {
  partyId: string
  documents: readonly DocumentResponse[]
}

/** Download one document; each download is audited as `VIEW_PII` by the API. */
function DownloadButton({ partyId, document: doc, onError }: {
  partyId: string
  document: DocumentResponse
  onError: (error: unknown) => void
}) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  async function download() {
    setBusy(true)
    try {
      await downloadDocument(partyId, doc.id, documentFilename(doc))
    } catch (error) {
      onError(error)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void download()}>
      <Download aria-hidden />
      {t('parties.documents.download')}
    </Button>
  )
}

/** The upload form: the file is checked here (type and size) before anything is sent. */
function UploadForm({ partyId }: { partyId: string }) {
  const { t } = useTranslation()
  const upload = useUploadDocument(partyId)
  const fileInput = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [docType, setDocType] = useState<DocumentType>('IDENTITY')
  const [problem, setProblem] = useState<ValidationKey | null>(null)

  function choose(next: File | null) {
    setFile(next)
    setProblem(next === null ? null : documentProblem(next))
    upload.reset()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (file === null) return setProblem('required')
    const refused = documentProblem(file)
    if (refused !== null) return setProblem(refused)
    upload.mutate(
      { file, docType, name: file.name, size: file.size, lastModified: file.lastModified },
      {
        onSuccess: () => {
          toast.success(t('parties.documents.uploaded'))
          setFile(null)
          upload.reset()
          if (fileInput.current) fileInput.current.value = ''
        },
      },
    )
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-3 border-t pt-4">
      {upload.isError && <FormAlert error={upload.error} />}
      <div className="grid items-end gap-4 sm:grid-cols-[1fr_14rem_auto]">
        <Field label={t('parties.documents.file')} error={problem ?? undefined}>
          {(aria) => (
            <input
              {...aria}
              ref={fileInput}
              type="file"
              accept={ALLOWED_DOCUMENT_TYPES.join(',')}
              disabled={upload.isPending}
              onChange={(event) => choose(event.target.files?.[0] ?? null)}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border file:bg-background file:px-3 file:py-1 file:text-sm"
            />
          )}
        </Field>
        <SelectField
          label={t('parties.documents.typeLabel')}
          value={docType}
          options={DOCUMENT_TYPES}
          optionLabel={(type) => t(`parties.documents.type.${type}`)}
          onChange={setDocType}
          disabled={upload.isPending}
        />
        <Button type="submit" disabled={upload.isPending}>
          {upload.isPending ? t('parties.documents.uploading') : t('parties.documents.upload')}
        </Button>
      </div>
    </form>
  )
}

/** A party's documents: the list with a download each and, for writers, the upload form. */
export function DocumentsCard({ partyId, documents }: DocumentsCardProps) {
  const { t } = useTranslation()
  const format = useFormat()
  const { hasRole } = useAuth()
  const [downloadError, setDownloadError] = useState<unknown>(null)

  const columns: Column<DocumentResponse>[] = [
    { id: 'type', header: t('parties.documents.typeLabel'), cell: (doc) => t(`parties.documents.type.${doc.doc_type}`) },
    {
      id: 'size',
      header: t('parties.documents.size'),
      cell: (doc) => format.number(doc.size),
      className: 'text-right tabular-nums',
    },
    { id: 'uploadedAt', header: t('parties.documents.uploadedAt'), cell: (doc) => <DateTimeCell value={doc.uploaded_at} /> },
    {
      id: 'download',
      header: <span className="sr-only">{t('parties.documents.download')}</span>,
      cell: (doc) => <DownloadButton partyId={partyId} document={doc} onError={setDownloadError} />,
      className: 'text-right',
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('parties.documents.title')}</CardTitle>
        <CardDescription>{t('parties.documents.downloadNotice')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {downloadError !== null && <FormAlert error={downloadError} />}
        <DataTable
          label={t('parties.documents.title')}
          columns={columns}
          rows={documents}
          getRowId={(doc) => doc.id}
          empty={t('parties.documents.empty')}
        />
        {hasRole(ACCESS.partiesWrite) && <UploadForm partyId={partyId} />}
      </CardContent>
    </Card>
  )
}
