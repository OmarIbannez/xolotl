import { describe, expect, it } from 'vitest'
import { apiKeySchema, isValidScope, PRESET_SCOPES, toApiKeyRequest } from './form'

// The grammar is the API's (SCOPE_RE in src/xolotl/modules/tenants/service.py): <area>:<read|write>,
// the area lowercase letters and underscores, starting with a letter. A key the console lets through
// but the API refuses comes back as a bare 422, with no hint about what was wrong.
const ACCEPTED = ['tenants:read', 'tenants:write', 'a:write', 'audit_log:read', 'reports:read', 'a_b_c:write']

const REFUSED: [scope: string, why: string][] = [
  ['Tenants:read', 'a capital in the area'],
  ['TENANTS:READ', 'all capitals'],
  ['tenants:READ', 'a capital in the action'],
  ['tenants:admin', 'an action that is not read or write'],
  ['tenants:delete', 'an action that is not read or write'],
  ['tenants:readwrite', 'two actions in one'],
  ['tenants:', 'no action'],
  [':read', 'no area'],
  ['tenants', 'no colon'],
  ['a-b:read', 'a hyphen is not in the area alphabet'],
  ['a b:read', 'a space in the area'],
  ['1a:read', 'the area starts with a digit'],
  ['_a:read', 'the area starts with an underscore'],
  ['área:read', 'not ASCII'],
  ['tenants:read ', 'a trailing space'],
  [' tenants:read', 'a leading space'],
  ['tenants:read\n', 'a trailing newline'],
  ['tenants:read:write', 'a second colon'],
  ['', 'empty'],
]

describe('the scope grammar', () => {
  it.each(ACCEPTED)('accepts %j', (scope) => {
    expect(isValidScope(scope)).toBe(true)
  })

  it.each(REFUSED)('refuses %j (%s)', (scope) => {
    expect(isValidScope(scope)).toBe(false)
  })

  it('fits every scope the dialog offers as a checkbox', () => {
    for (const { scope } of PRESET_SCOPES) expect(isValidScope(scope), scope).toBe(true)
  })
})

describe('apiKeySchema', () => {
  const valid = { name: 'ci-bot', scopes: ['tenants:read'], scopeDraft: '' }

  /** What the form would report: where, and the catalogue key of the message. */
  function problems(input: unknown) {
    const result = apiKeySchema.safeParse(input)
    return result.success ? [] : result.error.issues.map((issue) => ({ at: issue.path.join('.'), key: issue.message }))
  }

  it('accepts a named key whose scopes fit', () => {
    expect(problems({ ...valid, scopes: ['tenants:read', 'tenants:write', 'reports:read'] })).toEqual([])
  })

  it('points at the scope that does not fit, with the format message', () => {
    expect(problems({ ...valid, scopes: ['tenants:read', 'Tenants:read'] })).toEqual([{ at: 'scopes.1', key: 'scopeFormat' }])
    expect(problems({ ...valid, scopes: ['tenants:admin'] })).toEqual([{ at: 'scopes.0', key: 'scopeFormat' }])
  })

  it('needs at least one scope', () => {
    expect(problems({ ...valid, scopes: [] })).toEqual([{ at: 'scopes', key: 'scopesRequired' }])
  })

  it('needs a name, after trimming, of at most 120 characters', () => {
    expect(problems({ ...valid, name: '   ' })).toEqual([{ at: 'name', key: 'required' }])
    expect(problems({ ...valid, name: 'x'.repeat(120) })).toEqual([])
    expect(problems({ ...valid, name: 'x'.repeat(121) })).toEqual([{ at: 'name', key: 'nameTooLong' }])
  })

  it('does not let a scope that was typed but not added through', () => {
    expect(problems({ ...valid, scopeDraft: 'reports:read' })).toEqual([{ at: 'scopeDraft', key: 'scopeDraftPending' }])
    expect(problems({ ...valid, scopeDraft: '   ' })).toEqual([]) // blanks are not a scope
  })

  it('turns a valid form into the request: trimmed name, the scopes, and the tenant environment', () => {
    const parsed = apiKeySchema.parse({ ...valid, name: '  ci-bot  ' })

    expect(toApiKeyRequest(parsed, 'SANDBOX')).toEqual({ name: 'ci-bot', scopes: ['tenants:read'], env: 'SANDBOX' })
  })
})
