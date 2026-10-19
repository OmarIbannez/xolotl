import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { EmptyBlock } from './states'

export interface Column<Row> {
  /** Stable id of the column: its React key. */
  id: string
  header: ReactNode
  cell: (row: Row) => ReactNode
  /** Classes for the header and every cell of the column (alignment, width). */
  className?: string
}

interface DataTableProps<Row> {
  /** Names the table for assistive technology. */
  label: string
  columns: readonly Column<Row>[]
  /** The rows to show. Undefined while nothing has arrived yet. */
  rows: readonly Row[] | undefined
  getRowId: (row: Row) => string
  /** True until the first rows arrive: shows the header over placeholder rows. */
  loading?: boolean
  /** Shown instead of the table once it has loaded with no rows. */
  empty: ReactNode
  rowClassName?: (row: Row) => string | undefined
  loadingRows?: number
}

/**
 * A typed table over the shadcn `Table`, in three states: loading (the header over skeleton
 * rows), empty (a notice instead of an empty grid) and rows.
 */
export function DataTable<Row>({
  label,
  columns,
  rows,
  getRowId,
  loading = false,
  empty,
  rowClassName,
  loadingRows = 5,
}: DataTableProps<Row>) {
  const { t } = useTranslation()

  if (loading || rows === undefined) {
    return (
      <>
        <p role="status" className="sr-only">
          {t('common.loading')}
        </p>
        <Table aria-label={label} aria-busy="true">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              {columns.map((column) => (
                <TableHead key={column.id} className={column.className}>
                  {column.header}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {Array.from({ length: loadingRows }, (_, index) => (
              <TableRow key={index} className="hover:bg-transparent">
                {columns.map((column) => (
                  <TableCell key={column.id} className={column.className}>
                    <Skeleton className="h-4 w-full min-w-12" />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </>
    )
  }

  if (rows.length === 0) return <EmptyBlock>{empty}</EmptyBlock>

  return (
    <Table aria-label={label}>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {columns.map((column) => (
            <TableHead key={column.id} className={column.className}>
              {column.header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={getRowId(row)} className={cn(rowClassName?.(row))}>
            {columns.map((column) => (
              <TableCell key={column.id} className={column.className}>
                {column.cell(row)}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
