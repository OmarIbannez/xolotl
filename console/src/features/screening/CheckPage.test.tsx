// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Link, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { overview } from '@/features/lists/fixtures'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { CheckPage } from './CheckPage'
import { checkResult } from './fixtures'

const text = esMX.screening.check
const CHECK = 'POST /v1/screening/checks'
const LISTS = 'GET /v1/lists'
const NAME = 'Juan Pérez López'
const RFC = 'PELJ760510AB8'

function setup(routes: FakeRoutes = {}) {
  return renderApp(
    <>
      <nav>
        <Link to="/elsewhere">leave</Link>
        <Link to="/screening/check">back</Link>
      </nav>
      <Routes>
        <Route path="/screening/check" element={<CheckPage />} />
        <Route path="/elsewhere" element={<p>another page</p>} />
      </Routes>
    </>,
    {
      path: '/screening/check',
      me: makeMe({ roles: ['ANALYST'] }),
      routes: {
        [LISTS]: () =>
          json({
            items: [
              overview({ code: 'OFAC_SDN' }),
              overview({ code: 'UN_CONSOLIDATED' }),
              overview({ code: 'EU_FSF', enabled: false }),
              overview({ code: 'MX_LPB', active_seq: null }),
            ],
          }),
        [CHECK]: () => json(checkResult(), { status: 201 }),
        ...routes,
      },
    },
  )
}

const input = (label: string) => screen.getByLabelText(label)
const name1 = () => input(esMX.screening.check.name.replace('{{n}}', '1'))

beforeEach(() => {
  stubPointerCapture()
})

describe('CheckPage', () => {
  it('a person check posts the subject and shows the status', async () => {
    const { user, fakeApi } = setup()

    await user.type(name1(), NAME)
    await user.type(input(text.givenNames), 'Juan')
    await user.type(input(text.firstSurname), 'Pérez')
    await user.type(input(text.birthDate), '1976-05-10')
    await chooseOption(user, input(text.gender.label), text.gender.MALE)
    await user.type(input(text.nationalities), 'mx')
    await user.click(screen.getByRole('button', { name: text.addIdentifier }))
    await user.type(input(text.identifierValue.replace('{{n}}', '1')), RFC)
    const lists = await screen.findByRole('group', { name: text.lists })
    expect(within(lists).queryByText('EU_FSF')).toBeNull() // disabled
    expect(within(lists).queryByText('MX_LPB')).toBeNull() // no active version
    await user.click(within(lists).getByLabelText('OFAC_SDN'))
    await user.click(screen.getByRole('button', { name: text.submit }))

    const status = await screen.findByText(esMX.screening.subjectStatus.PENDING_REVIEW)
    expect(status).toBeTruthy()
    const [request] = fakeApi.calls(CHECK)
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await request?.json()).toEqual({
      subject: {
        type: 'PERSON',
        names: [NAME],
        given_names: 'Juan',
        first_surname: 'Pérez',
        birth_date: '1976-05-10',
        gender: 'MALE',
        nationalities: ['MX'],
        identifiers: [{ id_type: 'RFC', value: RFC }],
      },
      lists: ['OFAC_SDN'],
    })
    const hits = screen.getByRole('table', { name: esMX.screening.hits.label })
    expect(within(hits).getByRole('link', { name: esMX.screening.hits.openFinding }).getAttribute('href')).toBe(
      '/screening/matches/m1',
    )
    // Nothing typed reaches the URL.
    expect(window.location.href).not.toContain(encodeURIComponent('Pérez'))
  })

  it('an organisation check sends its names only', async () => {
    const { user, fakeApi } = setup()

    await user.click(screen.getByRole('button', { name: esMX.parties.type.ORGANIZATION }))
    expect(screen.queryByLabelText(text.givenNames)).toBeNull()
    await user.type(name1(), 'Acme SA de CV')
    await user.click(screen.getByRole('button', { name: text.addName }))
    await user.type(input(text.name.replace('{{n}}', '2')), 'Acme')
    await user.click(screen.getByRole('button', { name: text.submit }))

    await screen.findByText(esMX.screening.subjectStatus.PENDING_REVIEW)
    expect(await fakeApi.calls(CHECK)[0]?.json()).toEqual({ subject: { type: 'ORGANIZATION', names: ['Acme SA de CV', 'Acme'] } })
  })

  it('validation errors map to fields', async () => {
    const { user, fakeApi } = setup({
      [CHECK]: () =>
        problem(422, 'VALIDATION_FAILED', {
          errors: [{ loc: ['body', 'subject', 'birth_date'], msg: 'Input should be a valid date', type: 'date_from_datetime_parsing' }],
        }),
    })

    await user.click(screen.getByRole('button', { name: text.submit }))

    expect(await screen.findByText(esMX.validation.required)).toBeTruthy()
    expect(name1().getAttribute('aria-invalid')).toBe('true')
    expect(fakeApi.calls(CHECK)).toHaveLength(0)

    await user.type(name1(), NAME)
    await user.type(input(text.birthDate), '1976-05-10')
    await user.click(screen.getByRole('button', { name: text.submit }))

    await waitFor(() => expect(input(text.birthDate).getAttribute('aria-invalid')).toBe('true'))
    expect(screen.getByText(esMX.validation.invalid)).toBeTruthy()
    expect(screen.queryByText(esMX.screening.subjectStatus.PENDING_REVIEW)).toBeNull()
  })

  it('an invalid identifier is refused as a whole', async () => {
    const { user } = setup({ [CHECK]: () => problem(422, 'IDENTIFIER_INVALID') })

    await user.type(name1(), NAME)
    await user.click(screen.getByRole('button', { name: text.submit }))

    expect(await screen.findByText(text.identifierInvalid)).toBeTruthy()
  })

  it('the result is forgotten on leaving', async () => {
    const { user, queryClient } = setup()
    await user.type(name1(), NAME)
    await user.click(screen.getByRole('button', { name: text.submit }))
    await screen.findByText(esMX.screening.subjectStatus.PENDING_REVIEW)

    await user.click(screen.getByRole('link', { name: 'leave' }))
    await screen.findByText('another page')
    await user.click(screen.getByRole('link', { name: 'back' }))

    expect((name1() as HTMLInputElement).value).toBe('')
    expect(screen.queryByText(esMX.screening.subjectStatus.PENDING_REVIEW)).toBeNull()
    await waitFor(() => expect(queryClient.getMutationCache().getAll()).toEqual([]))
    const cached = JSON.stringify(queryClient.getQueryCache().getAll().map((query) => [query.queryKey, query.state.data]))
    expect(cached).not.toContain(NAME)
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(NAME)
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(NAME)
  })

  it('clear empties the form and drops the result', async () => {
    const { user } = setup()
    await user.type(name1(), NAME)
    await user.click(screen.getByRole('button', { name: text.submit }))
    await screen.findByText(esMX.screening.subjectStatus.PENDING_REVIEW)

    await user.click(screen.getByRole('button', { name: text.clear }))

    expect((name1() as HTMLInputElement).value).toBe('')
    expect(screen.queryByText(esMX.screening.subjectStatus.PENDING_REVIEW)).toBeNull()
  })
})
