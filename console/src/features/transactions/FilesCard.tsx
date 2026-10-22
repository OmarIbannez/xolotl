import { Download, Upload } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { FileStatus, FileStatusResponse } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { useAuth } from '@/auth/use-auth'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { KeyValues } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { useFormat } from '@/i18n/use-locale'
import { downloadFileErrors, FILE_POLL_MS, useTransactionFile } from './queries'
import { UploadDialog } from './UploadDialog'

const FILE_STATUS_VARIANT: Record<FileStatus, 'muted' | 'info' | 'good' | 'critical'> = {
  UPLOADED: 'muted',
  PROCESSING: 'info',
  COMPLETED: 'good',
  FAILED: 'critical',
}

/** Download the row-error report; codes only, never a cell value. */
function ErrorsButton({ fileId }: { fileId: string }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  async function download() {
    setBusy(true)
    setError(null)
    try {
      await downloadFileErrors(fileId)
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-2">
      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void download()}>
        <Download aria-hidden />
        {t('transactions.files.downloadErrors')}
      </Button>
      {error !== null && <FormAlert error={error} />}
    </div>
  )
}

/** The uploaded file's status, read again while the worker processes it. */
function FileStatusPanel({ initial, pollMs }: { initial: FileStatusResponse; pollMs: number }) {
  const { t } = useTranslation()
  const format = useFormat()
  const status = useTransactionFile(initial.file_id, initial, pollMs)
  const file = status.data ?? initial

  return (
    <section aria-label={t('transactions.files.statusLabel')} aria-live="polite" className="space-y-3 border-t pt-4">
      <KeyValues
        items={[
          [
            t('transactions.files.fileId'),
            <span key="id" className="font-mono text-xs">
              {file.file_id}
            </span>,
          ],
          [
            t('transactions.files.status'),
            <Badge key="status" variant={FILE_STATUS_VARIANT[file.status]}>
              {t(`transactions.files.fileStatus.${file.status}`)}
            </Badge>,
          ],
          [t('transactions.files.rowsTotal'), file.rows_total === null ? '—' : format.number(file.rows_total)],
          [t('transactions.files.rowsAccepted'), format.number(file.rows_accepted)],
          [t('transactions.files.rowsDuplicate'), format.number(file.rows_duplicate)],
          [t('transactions.files.rowsRejected'), format.number(file.rows_rejected)],
          [t('transactions.files.uploadedAt'), <DateTimeCell key="uploaded" value={file.uploaded_at} />],
          [
            t('transactions.files.finishedAt'),
            file.finished_at === null ? '—' : <DateTimeCell key="finished" value={file.finished_at} />,
          ],
          ...(file.last_error === null
            ? []
            : [
                [
                  t('transactions.files.lastError'),
                  <span key="error" className="font-mono text-xs">
                    {file.last_error}
                  </span>,
                ] as [string, ReactNode],
              ]),
        ]}
      />
      {status.isError && <ErrorBlock error={status.error} onRetry={() => void status.refetch()} />}
      {file.rows_rejected > 0 && <ErrorsButton fileId={file.file_id} />}
    </section>
  )
}

/**
 * The files panel: "Subir archivo" (writers) with the template and the client checks, then the
 * status of the file uploaded in this session, polled until the worker finishes it, with
 * "Descargar errores" when rows were rejected.
 */
export function FilesCard({ pollMs = FILE_POLL_MS }: { pollMs?: number }) {
  const { t } = useTranslation()
  const { hasRole } = useAuth()
  const [uploading, setUploading] = useState(false)
  const [uploaded, setUploaded] = useState<FileStatusResponse | null>(null)

  return (
    <Card className="animate-fade-in-up">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="space-y-1.5">
          <CardTitle>{t('transactions.files.title')}</CardTitle>
          <CardDescription>{t('transactions.files.description')}</CardDescription>
        </div>
        {hasRole(ACCESS.transactionsWrite) && (
          <Button type="button" variant="outline" size="sm" onClick={() => setUploading(true)}>
            <Upload aria-hidden />
            {t('transactions.files.upload')}
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {uploaded === null ? (
          <p className="text-sm text-muted-foreground">{t('transactions.files.none')}</p>
        ) : (
          <FileStatusPanel key={uploaded.file_id} initial={uploaded} pollMs={pollMs} />
        )}
      </CardContent>
      {uploading && (
        <UploadDialog onUploaded={({ file }) => setUploaded(file)} onClose={() => setUploading(false)} />
      )}
    </Card>
  )
}
