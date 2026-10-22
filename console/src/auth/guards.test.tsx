// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { makeAuth, makeUser } from '@/test/render'
import { ACCESS } from './access'
import { RequireRole } from './guards'
import { AuthContext } from './use-auth'

/** The admin pages, the parties area, the lists, the private lists, the screening queue and the ad hoc check behind the guards the console's routes use. */
function visit(path: string, roles: Role[]) {
  render(
    <AuthContext value={makeAuth(makeUser({ roles }))}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<RequireRole roles={ACCESS.apiKeys} />}>
            <Route path="/api-keys" element={<p>page of api keys</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.staff} />}>
            <Route path="/staff" element={<p>page of staff</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.connectors} />}>
            <Route path="/connectors" element={<p>page of connectors</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.parties} />}>
            <Route path="/parties" element={<p>page of parties</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.lists} />}>
            <Route path="/lists" element={<p>page of lists</p>} />
            <Route path="/lists/:code" element={<p>page of a list</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.privateLists} />}>
            <Route path="/private-lists" element={<p>page of private lists</p>} />
            <Route path="/private-lists/:listId" element={<p>page of a private list</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.screening} />}>
            <Route path="/screening/matches" element={<p>page of matches</p>} />
            <Route path="/screening/matches/:matchId" element={<p>page of a match</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.screeningCheck} />}>
            <Route path="/screening/check" element={<p>page of the ad hoc check</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.transactions} />}>
            <Route path="/transactions" element={<p>page of transactions</p>} />
            <Route path="/transactions/:transactionId" element={<p>page of a transaction</p>} />
          </Route>
          <Route element={<RequireRole roles={ACCESS.monitoring} />}>
            <Route path="/monitoring/hits" element={<p>page of rule hits</p>} />
            <Route path="/monitoring/hits/:hitId" element={<p>page of a rule hit</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthContext>,
  )
}

describe('the API keys, staff and connectors routes are for ADMIN only', () => {
  it.each([
    ['/api-keys', 'page of api keys'],
    ['/staff', 'page of staff'],
    ['/connectors', 'page of connectors'],
  ])('lets an administrator open %s', (path, page) => {
    visit(path, ['ADMIN'])

    expect(screen.getByText(page)).toBeTruthy()
    expect(screen.queryByText(esMX.forbidden.title)).toBeNull()
  })

  it.each([
    ['/api-keys', 'page of api keys', 'ANALYST'],
    ['/api-keys', 'page of api keys', 'SENIOR_ANALYST'],
    ['/api-keys', 'page of api keys', 'MLRO'],
    ['/api-keys', 'page of api keys', 'AUDITOR_RO'],
    ['/staff', 'page of staff', 'ANALYST'],
    ['/staff', 'page of staff', 'SENIOR_ANALYST'],
    ['/staff', 'page of staff', 'MLRO'],
    ['/staff', 'page of staff', 'AUDITOR_RO'],
    ['/connectors', 'page of connectors', 'ANALYST'],
    ['/connectors', 'page of connectors', 'SENIOR_ANALYST'],
    ['/connectors', 'page of connectors', 'MLRO'],
    ['/connectors', 'page of connectors', 'AUDITOR_RO'],
  ] as const)('shows "no access" instead of %s to a user who is only %s', (path, page, role) => {
    visit(path, [role])

    expect(screen.getByText(esMX.forbidden.title)).toBeTruthy()
    expect(screen.queryByText(page)).toBeNull()
  })

  it('lets a user in who holds ADMIN among other roles', () => {
    visit('/staff', ['MLRO', 'ADMIN'])

    expect(screen.getByText('page of staff')).toBeTruthy()
  })
})

describe('the parties area is open to every staff role', () => {
  it.each(['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'AUDITOR_RO', 'ADMIN'] as const)('lets a user who is %s open /parties', (role) => {
    visit('/parties', [role])

    expect(screen.getByText('page of parties')).toBeTruthy()
    expect(screen.queryByText(esMX.forbidden.title)).toBeNull()
  })

  it('shows "no access" to a user with no role', () => {
    visit('/parties', [])

    expect(screen.getByText(esMX.forbidden.title)).toBeTruthy()
    expect(screen.queryByText('page of parties')).toBeNull()
  })
})

describe('the lists are open to every staff role', () => {
  it.each([
    ['/lists', 'page of lists', 'ANALYST'],
    ['/lists', 'page of lists', 'SENIOR_ANALYST'],
    ['/lists', 'page of lists', 'MLRO'],
    ['/lists', 'page of lists', 'AUDITOR_RO'],
    ['/lists', 'page of lists', 'ADMIN'],
    ['/lists/UN_CONSOLIDATED', 'page of a list', 'ANALYST'],
    ['/lists/UN_CONSOLIDATED', 'page of a list', 'AUDITOR_RO'],
  ] as const)('lets a user open %s as %s', (path, page, role) => {
    visit(path, [role])

    expect(screen.getByText(page)).toBeTruthy()
    expect(screen.queryByText(esMX.forbidden.title)).toBeNull()
  })

  it.each(['/lists', '/lists/UN_CONSOLIDATED'])('shows "no access" at %s to a user with no role', (path) => {
    visit(path, [])

    expect(screen.getByText(esMX.forbidden.title)).toBeTruthy()
  })
})

describe('the private lists are for MLRO and ADMIN only', () => {
  it.each([
    ['/private-lists', 'page of private lists', 'MLRO'],
    ['/private-lists', 'page of private lists', 'ADMIN'],
    ['/private-lists/l1', 'page of a private list', 'MLRO'],
    ['/private-lists/l1', 'page of a private list', 'ADMIN'],
  ] as const)('lets a user open %s as %s', (path, page, role) => {
    visit(path, [role])

    expect(screen.getByText(page)).toBeTruthy()
    expect(screen.queryByText(esMX.forbidden.title)).toBeNull()
  })

  it.each([
    ['/private-lists', 'ANALYST'],
    ['/private-lists', 'SENIOR_ANALYST'],
    ['/private-lists', 'AUDITOR_RO'],
    ['/private-lists/l1', 'ANALYST'],
    ['/private-lists/l1', 'AUDITOR_RO'],
  ] as const)('shows "no access" at %s to %s', (path, role) => {
    visit(path, [role])

    expect(screen.getByText(esMX.forbidden.title)).toBeTruthy()
    expect(screen.queryByText(/page of/)).toBeNull()
  })
})

describe('the screening queue and its findings are open to every staff role', () => {
  it.each([
    ['/screening/matches', 'page of matches', 'ANALYST'],
    ['/screening/matches', 'page of matches', 'SENIOR_ANALYST'],
    ['/screening/matches', 'page of matches', 'MLRO'],
    ['/screening/matches', 'page of matches', 'AUDITOR_RO'],
    ['/screening/matches', 'page of matches', 'ADMIN'],
    ['/screening/matches/m1', 'page of a match', 'ANALYST'],
    ['/screening/matches/m1', 'page of a match', 'AUDITOR_RO'],
  ] as const)('lets a user open %s as %s', (path, page, role) => {
    visit(path, [role])

    expect(screen.getByText(page)).toBeTruthy()
    expect(screen.queryByText(esMX.forbidden.title)).toBeNull()
  })

  it.each(['/screening/matches', '/screening/matches/m1'])('shows "no access" at %s to a user with no role', (path) => {
    visit(path, [])

    expect(screen.getByText(esMX.forbidden.title)).toBeTruthy()
    expect(screen.queryByText(/page of/)).toBeNull()
  })
})

describe('the ad hoc check is open to every role that writes', () => {
  it.each(['ANALYST', 'SENIOR_ANALYST', 'MLRO', 'ADMIN'] as const)('lets %s open it', (role) => {
    visit('/screening/check', [role])

    expect(screen.getByText('page of the ad hoc check')).toBeTruthy()
  })

  it.each([[['AUDITOR_RO']], [[]]] as const)('shows "no access" to %j', (roles) => {
    visit('/screening/check', [...roles])

    expect(screen.getByText(esMX.forbidden.title)).toBeTruthy()
    expect(screen.queryByText(/page of/)).toBeNull()
  })
})

describe('the transactions are open to every staff role', () => {
  it.each([
    ['/transactions', 'page of transactions', 'ANALYST'],
    ['/transactions', 'page of transactions', 'SENIOR_ANALYST'],
    ['/transactions', 'page of transactions', 'MLRO'],
    ['/transactions', 'page of transactions', 'AUDITOR_RO'],
    ['/transactions', 'page of transactions', 'ADMIN'],
    ['/transactions/t1', 'page of a transaction', 'ANALYST'],
    ['/transactions/t1', 'page of a transaction', 'AUDITOR_RO'],
  ] as const)('lets a user open %s as %s', (path, page, role) => {
    visit(path, [role])

    expect(screen.getByText(page)).toBeTruthy()
    expect(screen.queryByText(esMX.forbidden.title)).toBeNull()
  })

  it.each(['/transactions', '/transactions/t1'])('shows "no access" at %s to a user with no role', (path) => {
    visit(path, [])

    expect(screen.getByText(esMX.forbidden.title)).toBeTruthy()
    expect(screen.queryByText(/page of/)).toBeNull()
  })
})

describe('the rule hits are open to every staff role', () => {
  it.each([
    ['/monitoring/hits', 'page of rule hits', 'ANALYST'],
    ['/monitoring/hits', 'page of rule hits', 'SENIOR_ANALYST'],
    ['/monitoring/hits', 'page of rule hits', 'MLRO'],
    ['/monitoring/hits', 'page of rule hits', 'AUDITOR_RO'],
    ['/monitoring/hits', 'page of rule hits', 'ADMIN'],
    ['/monitoring/hits/h1', 'page of a rule hit', 'ANALYST'],
    ['/monitoring/hits/h1', 'page of a rule hit', 'AUDITOR_RO'],
  ] as const)('lets a user open %s as %s', (path, page, role) => {
    visit(path, [role])

    expect(screen.getByText(page)).toBeTruthy()
    expect(screen.queryByText(esMX.forbidden.title)).toBeNull()
  })

  it.each(['/monitoring/hits', '/monitoring/hits/h1'])('shows "no access" at %s to a user with no role', (path) => {
    visit(path, [])

    expect(screen.getByText(esMX.forbidden.title)).toBeTruthy()
    expect(screen.queryByText(/page of/)).toBeNull()
  })
})
