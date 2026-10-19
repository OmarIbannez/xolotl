import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/errors'
import en from './en.json'
import esMX from './es-MX.json'

let i18n: (typeof import('./index'))['default']
let describeError: (typeof import('./errors'))['describeError']

beforeAll(async () => {
  // The module keeps <html lang> in step with the language; give it something to write to.
  vi.stubGlobal('document', { documentElement: { lang: '' } })
  i18n = (await import('./index')).default
  describeError = (await import('./errors')).describeError
  await i18n.changeLanguage('es-MX')
})

afterAll(() => {
  vi.unstubAllGlobals()
})

const problem = (status: number, code: string, requestId?: string) =>
  new ApiError({ status, code, detail: 'English detail from the server', requestId })

describe('describeError', () => {
  it('maps a problem code to its message in the active language', () => {
    expect(describeError(i18n.t, problem(403, 'ROLE_FORBIDDEN')).message).toBe(esMX.errors.ROLE_FORBIDDEN)
  })

  it('follows a language switch', async () => {
    await i18n.changeLanguage('en')
    expect(describeError(i18n.t, problem(403, 'ROLE_FORBIDDEN')).message).toBe(en.errors.ROLE_FORBIDDEN)
    await i18n.changeLanguage('es-MX')
  })

  it('carries the request id as the reference', () => {
    expect(describeError(i18n.t, problem(500, 'INTERNAL', 'req-9')).reference).toBe('req-9')
    expect(describeError(i18n.t, problem(500, 'INTERNAL')).reference).toBeUndefined()
  })

  it('never shows the server detail', () => {
    const { message } = describeError(i18n.t, problem(409, 'CONFLICT'))
    expect(message).not.toContain('English detail')
  })

  it('falls back to the generic message for an unknown code', () => {
    expect(describeError(i18n.t, problem(418, 'TEAPOT')).message).toBe(esMX.errors.generic)
  })

  it('falls back to the internal-error message for an unknown 5xx code', () => {
    expect(describeError(i18n.t, problem(500, 'HTTP_500')).message).toBe(esMX.errors.INTERNAL)
  })

  it('says the server is unreachable for gateway errors and network failures', () => {
    for (const error of [problem(502, 'HTTP_502'), problem(503, 'HTTP_503'), problem(504, 'HTTP_504'), problem(0, 'NETWORK_ERROR')]) {
      expect(describeError(i18n.t, error).message).toBe(esMX.errors.NETWORK_ERROR)
    }
  })

  it('handles anything that is not an ApiError', () => {
    expect(describeError(i18n.t, new Error('boom'))).toEqual({ message: esMX.errors.generic, reference: undefined })
    expect(describeError(i18n.t, 'boom').message).toBe(esMX.errors.generic)
  })
})

describe('language detection', () => {
  it('resolves browser languages to a supported locale, defaulting to es-MX', () => {
    const utils = i18n.services.languageUtils
    expect(utils.getBestMatchFromCodes(['es-AR'])).toBe('es-MX')
    expect(utils.getBestMatchFromCodes(['es'])).toBe('es-MX')
    expect(utils.getBestMatchFromCodes(['en-US', 'en'])).toBe('en')
    expect(utils.getBestMatchFromCodes(['en-GB'])).toBe('en')
    expect(utils.getBestMatchFromCodes(['fr-FR', 'fr'])).toBe('es-MX')
  })
})
