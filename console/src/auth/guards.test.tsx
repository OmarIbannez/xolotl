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

/** The two admin pages behind the guards the console's routes use. */
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
        </Routes>
      </MemoryRouter>
    </AuthContext>,
  )
}

describe('the API keys and staff routes are for ADMIN only', () => {
  it.each([
    ['/api-keys', 'page of api keys'],
    ['/staff', 'page of staff'],
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
