import type { TFunction } from 'i18next'
import { isApiError } from '@/api/errors'
import esMX from './es-MX.json'

/** `errors.<CODE>` entries are the problem codes; the other keys are not. */
type ErrorCode = Exclude<keyof typeof esMX.errors, 'generic' | 'reference'>

const NOT_CODES = ['generic', 'reference']
const ERROR_CODES: ReadonlySet<string> = new Set(
  Object.keys(esMX.errors).filter((key) => !NOT_CODES.includes(key)),
)

function isErrorCode(code: string): code is ErrorCode {
  return ERROR_CODES.has(code)
}

export interface ErrorDescription {
  /** What to show the user, in the active language. */
  message: string
  /** The request id to quote to support, when the server gave one. */
  reference: string | undefined
}

/** A gateway or proxy answered for an API that is down or unreachable. */
const UNREACHABLE_STATUSES: readonly number[] = [502, 503, 504]

/**
 * Turn anything thrown by the API layer into a message for people. Problem codes map
 * to `errors.<CODE>`; an unknown code falls back to the generic message (or to the
 * internal-error one for 5xx, and to the unreachable one for gateway errors). The
 * server's English `detail` is never shown.
 */
export function describeError(t: TFunction, error: unknown): ErrorDescription {
  if (!isApiError(error)) return { message: t('errors.generic'), reference: undefined }
  let message: string
  if (isErrorCode(error.code)) message = t(`errors.${error.code}`)
  else if (UNREACHABLE_STATUSES.includes(error.status)) message = t('errors.NETWORK_ERROR')
  else if (error.status >= 500) message = t('errors.INTERNAL')
  else message = t('errors.generic')
  return { message, reference: error.requestId }
}
