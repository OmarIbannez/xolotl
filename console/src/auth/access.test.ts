import { describe, expect, it } from 'vitest'
import type { Role } from '@/api/types'
import { ACCESS, decideRoute, hasAnyRole, type RouteGuardInput } from './access'
import { pathOf, redirectTarget } from './redirect'

describe('hasAnyRole', () => {
  it('lets everyone in when no role is required', () => {
    expect(hasAnyRole([], [])).toBe(true)
    expect(hasAnyRole(['ANALYST'], [])).toBe(true)
  })

  it('requires at least one of the roles', () => {
    expect(hasAnyRole(['ANALYST', 'MLRO'], ACCESS.audit)).toBe(true)
    expect(hasAnyRole(['ANALYST', 'SENIOR_ANALYST'], ACCESS.audit)).toBe(false)
    expect(hasAnyRole([], ACCESS.staff)).toBe(false)
  })

  it('follows the access table: ADMIN manages keys, staff and connectors; MLRO, ADMIN and AUDITOR_RO read audit; every role reads parties, all but AUDITOR_RO write them, only MLRO sets a legal hold; every role reads the lists and only ADMIN toggles a subscription; MLRO and ADMIN manage the private lists; every role reads the screening queue, ANALYST, SENIOR_ANALYST and MLRO decide findings, only MLRO revokes a whitelist row, and every role but AUDITOR_RO runs a check on demand', () => {
    const allowed = (role: Role) => ({
      apiKeys: hasAnyRole([role], ACCESS.apiKeys),
      staff: hasAnyRole([role], ACCESS.staff),
      audit: hasAnyRole([role], ACCESS.audit),
      connectors: hasAnyRole([role], ACCESS.connectors),
      parties: hasAnyRole([role], ACCESS.parties),
      partiesWrite: hasAnyRole([role], ACCESS.partiesWrite),
      legalHold: hasAnyRole([role], ACCESS.legalHold),
      lists: hasAnyRole([role], ACCESS.lists),
      listsAdmin: hasAnyRole([role], ACCESS.listsAdmin),
      privateLists: hasAnyRole([role], ACCESS.privateLists),
      screening: hasAnyRole([role], ACCESS.screening),
      dispositions: hasAnyRole([role], ACCESS.dispositions),
      whitelistRevoke: hasAnyRole([role], ACCESS.whitelistRevoke),
      screeningCheck: hasAnyRole([role], ACCESS.screeningCheck),
    })

    expect(allowed('ADMIN')).toEqual({
      apiKeys: true,
      staff: true,
      audit: true,
      connectors: true,
      parties: true,
      partiesWrite: true,
      legalHold: false,
      lists: true,
      listsAdmin: true,
      privateLists: true,
      screening: true,
      dispositions: false,
      whitelistRevoke: false,
      screeningCheck: true,
    })
    expect(allowed('MLRO')).toEqual({
      apiKeys: false,
      staff: false,
      audit: true,
      connectors: false,
      parties: true,
      partiesWrite: true,
      legalHold: true,
      lists: true,
      listsAdmin: false,
      privateLists: true,
      screening: true,
      dispositions: true,
      whitelistRevoke: true,
      screeningCheck: true,
    })
    expect(allowed('AUDITOR_RO')).toEqual({
      apiKeys: false,
      staff: false,
      audit: true,
      connectors: false,
      parties: true,
      partiesWrite: false,
      legalHold: false,
      lists: true,
      listsAdmin: false,
      privateLists: false,
      screening: true,
      dispositions: false,
      whitelistRevoke: false,
      screeningCheck: false,
    })
    expect(allowed('ANALYST')).toEqual({
      apiKeys: false,
      staff: false,
      audit: false,
      connectors: false,
      parties: true,
      partiesWrite: true,
      legalHold: false,
      lists: true,
      listsAdmin: false,
      privateLists: false,
      screening: true,
      dispositions: true,
      whitelistRevoke: false,
      screeningCheck: true,
    })
    expect(allowed('SENIOR_ANALYST')).toEqual({
      apiKeys: false,
      staff: false,
      audit: false,
      connectors: false,
      parties: true,
      partiesWrite: true,
      legalHold: false,
      lists: true,
      listsAdmin: false,
      privateLists: false,
      screening: true,
      dispositions: true,
      whitelistRevoke: false,
      screeningCheck: true,
    })
  })
})

describe('the transactions access', () => {
  it('lets every staff role read the transactions, every role but AUDITOR_RO upload a file and only MLRO and ADMIN request a backfill', () => {
    const roles: Role[] = ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.transactions))).toEqual(roles)
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.transactionsWrite))).toEqual(['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'ADMIN'])
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.backfill))).toEqual(['MLRO', 'ADMIN'])
    expect(hasAnyRole([], ACCESS.transactions)).toBe(false)
  })
})

describe('the rails access', () => {
  it('lets only ADMIN open Admin › Rieles, as only ADMIN may change a fail mode', () => {
    const roles: Role[] = ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.rails))).toEqual(['ADMIN'])
  })
})

describe('the monitoring access', () => {
  it('lets every staff role read the rule hits and reveal them, and nobody without a role', () => {
    const roles: Role[] = ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.monitoring))).toEqual(roles)
    expect(hasAnyRole([], ACCESS.monitoring)).toBe(false)
  })
})

describe('the rules access', () => {
  it('lets every staff role read the rules, the senior analyst, MLRO and ADMIN write them and only MLRO and ADMIN approve', () => {
    const roles: Role[] = ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.rules))).toEqual(roles)
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.rulesWrite))).toEqual(['SENIOR_ANALYST', 'MLRO', 'ADMIN'])
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.rulesApprove))).toEqual(['MLRO', 'ADMIN'])
    expect(hasAnyRole([], ACCESS.rules)).toBe(false)
  })
})

describe('the captures access', () => {
  it('lets every staff role read the captures through the rules area and only MLRO and ADMIN close a period', () => {
    const roles: Role[] = ['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN']
    expect(roles.filter((role) => hasAnyRole([role], ACCESS.capturesClose))).toEqual(['MLRO', 'ADMIN'])
    expect(hasAnyRole([], ACCESS.capturesClose)).toBe(false)
  })
})

describe('redirectTarget', () => {
  it('returns the remembered in-console path, query included', () => {
    expect(redirectTarget({ from: '/staff' })).toBe('/staff')
    expect(redirectTarget({ from: '/audit?action=auth.login_failed' })).toBe('/audit?action=auth.login_failed')
  })

  it('goes home without a usable memory', () => {
    expect(redirectTarget(undefined)).toBe('/')
    expect(redirectTarget(null)).toBe('/')
    expect(redirectTarget({})).toBe('/')
    expect(redirectTarget({ from: 42 })).toBe('/')
    expect(redirectTarget('/staff')).toBe('/')
  })

  it('never leaves the console', () => {
    expect(redirectTarget({ from: 'https://evil.example/' })).toBe('/')
    expect(redirectTarget({ from: '//evil.example/' })).toBe('/')
    expect(redirectTarget({ from: 'staff' })).toBe('/')
  })

  it('never returns to a pre-session screen, which could loop', () => {
    expect(redirectTarget({ from: '/login' })).toBe('/')
    expect(redirectTarget({ from: '/setup-password' })).toBe('/')
    expect(redirectTarget({ from: '/setup-password?x=1' })).toBe('/')
  })

  it('pathOf keeps the path and the query', () => {
    expect(pathOf({ pathname: '/audit', search: '?limit=10' })).toBe('/audit?limit=10')
    expect(pathOf({ pathname: '/staff', search: '' })).toBe('/staff')
  })
})

const base: RouteGuardInput = {
  status: 'authenticated',
  mustChangePassword: false,
  endedBy: null,
  allowPasswordChange: false,
  path: '/staff',
  state: undefined,
}
const decide = (overrides: Partial<RouteGuardInput>) => decideRoute({ ...base, ...overrides })

describe('decideRoute', () => {
  it('waits while a stored session is being checked, and reports one that could not be', () => {
    expect(decide({ status: 'loading' })).toEqual({ kind: 'wait' })
    expect(decide({ status: 'error' })).toEqual({ kind: 'error' })
  })

  it('lets a signed-in user through', () => {
    expect(decide({})).toEqual({ kind: 'allow' })
  })

  it('sends an anonymous visitor to /login, remembering where they were headed', () => {
    expect(decide({ status: 'anonymous', path: '/audit?limit=10' })).toEqual({
      kind: 'redirect',
      to: '/login',
      state: { from: '/audit?limit=10' },
    })
  })

  it('sends a visitor whose session expired to /login, remembering the page', () => {
    expect(decide({ status: 'anonymous', endedBy: 'expired', path: '/staff' })).toEqual({
      kind: 'redirect',
      to: '/login',
      state: { from: '/staff' },
    })
  })

  it('sends a visitor who just signed out to /login without a memory', () => {
    expect(decide({ status: 'anonymous', endedBy: 'signedOut', path: '/staff' })).toEqual({
      kind: 'redirect',
      to: '/login',
    })
  })

  it('holds an account that must change its password at /setup-password', () => {
    expect(decide({ mustChangePassword: true, path: '/staff' })).toEqual({
      kind: 'redirect',
      to: '/setup-password',
      state: { from: '/staff' },
    })
  })

  it('lets that account reach /setup-password itself', () => {
    expect(decide({ mustChangePassword: true, allowPasswordChange: true, path: '/setup-password' })).toEqual({
      kind: 'allow',
    })
  })

  it('sends the account back where it was headed once the password is changed', () => {
    expect(
      decide({ allowPasswordChange: true, path: '/setup-password', state: { from: '/staff' } }),
    ).toEqual({ kind: 'redirect', to: '/staff' })
  })

  it('sends it home when it was not headed anywhere in particular', () => {
    expect(decide({ allowPasswordChange: true, path: '/setup-password' })).toEqual({
      kind: 'redirect',
      to: '/',
    })
  })

  it('walks a 403 PASSWORD_CHANGE_REQUIRED on /staff to /setup-password and back, without a loop', () => {
    // 1. the API answers 403 on a request made from /staff: the gate is raised
    const held = decide({ mustChangePassword: true, path: '/staff' })
    expect(held).toEqual({ kind: 'redirect', to: '/setup-password', state: { from: '/staff' } })

    // 2. the visitor lands on /setup-password with that memory and is let in
    expect(
      decide({ mustChangePassword: true, allowPasswordChange: true, path: '/setup-password', state: { from: '/staff' } }),
    ).toEqual({ kind: 'allow' })

    // 3. the password is changed: back to /staff, which is then allowed
    const back = decide({ allowPasswordChange: true, path: '/setup-password', state: { from: '/staff' } })
    expect(back).toEqual({ kind: 'redirect', to: '/staff' })
    expect(decide({ path: '/staff' })).toEqual({ kind: 'allow' })
  })

  it('walks a 401 on /staff to /login and back, without a loop', () => {
    // 1. the API answers 401: the session is cleared and the guard sends the visitor to /login
    const expired = decide({ status: 'anonymous', endedBy: 'expired', path: '/staff' })
    expect(expired).toEqual({ kind: 'redirect', to: '/login', state: { from: '/staff' } })

    // 2. after signing in again, the login page resumes at the remembered page
    expect(redirectTarget({ from: '/staff' })).toBe('/staff')
    expect(decide({ path: '/staff' })).toEqual({ kind: 'allow' })
  })
})
