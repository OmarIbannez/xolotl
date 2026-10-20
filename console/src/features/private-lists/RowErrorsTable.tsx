import { useTranslation } from 'react-i18next'
import { DataTable, type Column } from '@/components/common/DataTable'
import type { RowError } from './row-errors'

/** The `TemplateError` codes, each with a `privateLists.rowError.*` key. */
const CODES = [
  'EMPTY_FILE',
  'UNREADABLE_FILE',
  'MISSING_COLUMN',
  'UNKNOWN_COLUMN',
  'TOO_MANY_ROWS',
  'REQUIRED',
  'INVALID_TYPE',
  'NAME_AMBIGUOUS',
  'INVALID_DATE',
  'INVALID_RFC',
  'INVALID_CURP',
  'RFC_TYPE_MISMATCH',
  'INVALID_COUNTRY',
  'DUPLICATE_KEY',
] as const

const isKnown = (code: string): code is (typeof CODES)[number] => (CODES as readonly string[]).includes(code)

/**
 * Where each problem of a rejected upload is (row and column) and what it is, in words. The API
 * sends no cell value, and the server's English message is not shown either: the code is enough.
 */
export function RowErrorsTable({ errors }: { errors: readonly RowError[] }) {
  const { t } = useTranslation()
  const none = <span className="text-muted-foreground">—</span>
  const columns: Column<RowError & { index: number }>[] = [
    {
      id: 'row',
      header: t('privateLists.rowErrors.row'),
      cell: (error) => (error.row === null ? t('privateLists.rowErrors.wholeFile') : <span className="tabular">{error.row}</span>),
    },
    {
      id: 'column',
      header: t('privateLists.rowErrors.column'),
      cell: (error) => (error.column === null ? none : <span className="font-mono text-xs">{error.column}</span>),
    },
    {
      id: 'problem',
      header: t('privateLists.rowErrors.problem'),
      cell: (error) => (isKnown(error.code) ? t(`privateLists.rowError.${error.code}`) : t('privateLists.rowError.unknown')),
      className: 'whitespace-normal',
    },
  ]
  return (
    <DataTable
      label={t('privateLists.rowErrors.label')}
      columns={columns}
      rows={errors.map((error, index) => ({ ...error, index }))}
      getRowId={(error) => String(error.index)}
      empty={t('privateLists.rowErrors.wholeFile')}
    />
  )
}
