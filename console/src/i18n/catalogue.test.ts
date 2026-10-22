import { describe, expect, it } from 'vitest'
import en from './en.json'
import esMX from './es-MX.json'

type Tree = { [key: string]: string | Tree }

/** `{ a: { b: 'x' } }` becomes `Map { 'a.b' => 'x' }`. */
function flatten(tree: Tree, prefix = ''): Map<string, string> {
  const out = new Map<string, string>()
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (typeof value === 'string') out.set(path, value)
    else for (const [nested, text] of flatten(value, path)) out.set(nested, text)
  }
  return out
}

const spanish = flatten(esMX)
const english = flatten(en)

const placeholders = (text: string) => [...text.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]).sort()

/** Strings that read the same in both languages on purpose (a name, endonyms, a shared word). */
const SAME_IN_BOTH = new Set([
  'app.name',
  'env.SANDBOX',
  'language.es-MX',
  'language.en',
  'home.access.roles', // "Roles" is the same word in Spanish
  'staff.table.roles',
  'staff.create.roles',
  'common.no',
  'apiKeys.scopes.placeholder', // an example scope: an API identifier, not copy
  'connectors.mode.SANDBOX', // the product word for the environment, as env.SANDBOX
  'connectors.checkType.UNIT_VALUE', // API identifiers, not copy
  'connectors.checkType.RFC_VALIDATION',
  'connectors.checkType.CURP_VALIDATION',
  'parties.identifierType.CURP', // identifier acronyms: the same text in both languages
  'parties.identifierType.RFC',
  'parties.identifierType.CLABE',
  'parties.identifierType.INE',
  'parties.identifierType.CPF',
  'parties.identifierType.CNPJ',
  'parties.identifierType.CUIT',
  'parties.identifierType.RUT',
  'parties.identifierType.NIT',
  'lists.idType.RFC', // identifier acronyms on list entries, as parties.identifierType
  'lists.idType.CURP',
  'transactions.rail.SPEI', // payment rail names: the same text in both languages
  'transactions.rail.CODI',
  'transactions.rail.SWIFT',
  'transactions.channel.API',
  'rules.versions.version', // "v2": a version number, the same in both languages
])

describe('translation catalogues', () => {
  it('key every string in both locales', () => {
    expect([...spanish.keys()].sort()).toEqual([...english.keys()].sort())
  })

  it('have no empty strings', () => {
    for (const [key, text] of [...spanish, ...english]) {
      expect(text.trim(), key).not.toBe('')
    }
  })

  it('use the same interpolation placeholders in both locales', () => {
    for (const [key, text] of spanish) {
      expect(placeholders(english.get(key) ?? ''), key).toEqual(placeholders(text))
    }
  })

  it('translate rather than copy: only a few strings are identical across locales', () => {
    const identical = [...spanish].filter(([key, text]) => english.get(key) === text).map(([key]) => key)
    expect(identical.filter((key) => !SAME_IN_BOTH.has(key))).toEqual([])
  })

  it('cover every stable problem code of the API (spec section 7) and the client-side ones', () => {
    const codes = [
      'UNAUTHENTICATED',
      'ROLE_FORBIDDEN',
      'SCOPE_MISSING',
      'TENANT_SUSPENDED',
      'PASSWORD_CHANGE_REQUIRED',
      'FORBIDDEN',
      'NOT_FOUND',
      'CONFLICT',
      'BAD_REQUEST',
      'IDEMPOTENCY_KEY_REQUIRED',
      'IDEMPOTENCY_KEY_REUSED',
      'IDEMPOTENCY_IN_PROGRESS',
      'VALIDATION_FAILED',
      'CONNECTOR_NO_ROUTE',
      'UNIT_VALUE_MISSING',
      'POLICY_NOT_ASSIGNED',
      'POLICY_LOOSENING',
      'IDENTIFIER_EXISTS',
      'IDENTIFIER_INVALID',
      'OBJECT_CORRUPT',
      'LIST_MANDATORY',
      'LIST_MASS_REMOVAL',
      'LIST_FORMAT_INVALID',
      'LIST_CODE_UNSUPPORTED',
      'PRIVATE_LIST_INVALID',
      'PRIVATE_LIST_EXISTS',
      'DISPOSITION_NOT_ALLOWED',
      'SCREENING_INDEX_UNAVAILABLE',
      'TRANSACTION_BATCH_INVALID',
      'TRANSACTION_BATCH_TOO_LARGE',
      'EXTERNAL_ID_CONFLICT',
      'TRANSACTION_FILE_TOO_LARGE',
      'TRANSACTION_FILE_INVALID',
      'BACKFILL_RANGE_INVALID',
      'RAIL_UNKNOWN',
      'RULE_PARAM_OUT_OF_BOUNDS',
      'RULE_TRANSITION_NOT_ALLOWED',
      'SIMULATION_RANGE_TOO_LONG',
      'RULE_TEMPLATE_UNKNOWN',
      'PERIOD_NOT_ENDED',
      'INTERNAL',
      'NETWORK_ERROR',
    ]
    for (const code of codes) {
      expect(spanish.has(`errors.${code}`), `es-MX errors.${code}`).toBe(true)
      expect(english.has(`errors.${code}`), `en errors.${code}`).toBe(true)
    }
  })
})
