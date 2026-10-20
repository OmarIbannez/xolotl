/** The API's cap on an upload (`XOLOTL_PRIVATE_LIST_MAX_MB`, 20 MiB by default). */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

export const CSV_TYPE = 'text/csv'
export const XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

/** What the file picker offers: the extensions, since browsers type CSV files inconsistently. */
export const UPLOAD_ACCEPT = '.csv,.xlsx'

/**
 * The content type an upload is sent with. The extension decides it: browsers give a CSV
 * `text/csv`, `application/vnd.ms-excel` or nothing, depending on the system. Null when the file
 * is neither CSV nor XLSX.
 */
export function uploadContentType(file: Pick<File, 'name' | 'type'>): string | null {
  const name = file.name.toLowerCase()
  if (name.endsWith('.csv')) return CSV_TYPE
  if (name.endsWith('.xlsx')) return XLSX_TYPE
  if (file.type === CSV_TYPE || file.type === XLSX_TYPE) return file.type
  return null
}

/** Why a file cannot be sent, as a `privateLists.upload.*` key (`required` for an empty one). */
export function uploadProblem(file: Pick<File, 'name' | 'type' | 'size'>): 'wrongType' | 'tooLarge' | 'required' | null {
  if (uploadContentType(file) === null) return 'wrongType'
  if (file.size === 0) return 'required'
  if (file.size > MAX_UPLOAD_BYTES) return 'tooLarge'
  return null
}
