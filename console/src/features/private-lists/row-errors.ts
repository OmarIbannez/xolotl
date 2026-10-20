import type { ProblemFieldError } from '@/api/types'

/**
 * One problem of a rejected private-list upload, as the console shows it. The API locates each
 * problem and names its code; it never sends the cell's value, and neither does this.
 */
export interface RowError {
  /** The 1-based file row (the header is row 1); null for a problem of the whole file. */
  row: number | null
  /** The template column, or the column number of a value outside the template; null when none. */
  column: string | null
  /** The `TemplateError` code, for example `INVALID_RFC`. */
  code: string
}

/** A problem body or an `ApiError`: anything that carries the problem's `errors`. */
interface WithErrors {
  readonly errors?: readonly ProblemFieldError[] | undefined
}

function located(loc: readonly (string | number)[]): Pick<RowError, 'row' | 'column'> {
  const [scope, row, column] = loc
  if (scope !== 'row' || typeof row !== 'number' || !Number.isInteger(row) || loc.length > 3) {
    return { row: null, column: null }
  }
  return { row, column: column === undefined ? null : String(column) }
}

/**
 * The row errors of a 422 `PRIVATE_LIST_INVALID`: `loc` `["row", n, column]` gives the row and
 * column, `["file"]` (or a `loc` this console cannot read) neither; `type` is the code.
 */
export function rowErrorsOf(problem: WithErrors): RowError[] {
  return (problem.errors ?? []).map((error) => ({ ...located(error.loc), code: error.type }))
}
