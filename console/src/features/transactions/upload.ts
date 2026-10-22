/** What the API takes as a transactions file, by content type, with the suffixes of each. */
export const ACCEPTED = {
  'text/csv': ['.csv'],
  'application/x-ndjson': ['.jsonl'],
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
} as const

/** The API's cap on an upload (`XOLOTL_MONITORING_FILE_MAX_MB`, 50 MiB by default). */
export const MAX_FILE_MB = 50

const MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

type AcceptedType = keyof typeof ACCEPTED

const ACCEPTED_TYPES = Object.keys(ACCEPTED) as AcceptedType[]

/** The file picker's `accept`: the suffixes, since browsers type these files inconsistently. */
export const ACCEPT_ATTRIBUTE = ACCEPTED_TYPES.flatMap((type) => ACCEPTED[type]).join(',')

function isAccepted(type: string): type is AcceptedType {
  return (ACCEPTED_TYPES as string[]).includes(type)
}

/**
 * The content type an upload is sent with: the browser's when it is one of the three, otherwise
 * the one the suffix names (a CSV comes as `application/vnd.ms-excel` or untyped on some systems).
 * Null when the file is none of them.
 */
export function contentTypeFor(file: Pick<File, 'name' | 'type'>): string | null {
  if (isAccepted(file.type)) return file.type
  const name = file.name.toLowerCase()
  return ACCEPTED_TYPES.find((type) => ACCEPTED[type].some((suffix) => name.endsWith(suffix))) ?? null
}

/** Whether a file may be sent; checked before any request. */
export function checkUpload(file: Pick<File, 'name' | 'type' | 'size'>): 'ok' | 'tooLarge' | 'unsupported' {
  if (contentTypeFor(file) === null) return 'unsupported'
  if (file.size > MAX_FILE_BYTES) return 'tooLarge'
  return 'ok'
}
