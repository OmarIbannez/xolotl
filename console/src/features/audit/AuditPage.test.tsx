// @vitest-environment jsdom
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { Role } from '@/api/types'
import { ACCESS } from '@/auth/access'
import { RequireAuth, RequireRole } from '@/auth/guards'
import i18n from '@/i18n'
import en from '@/i18n/en.json'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { AuditPage } from './AuditPage'
import { makeEvent, makeEvents, pageOf, verification } from './fixtures'
import { AUDIT_PAGE_SIZE } from './queries'

const es = esMX.audit

/** The audit list, answering by `action|cursor` like the API would (anything else is empty). */
function auditList(pages: Record<string, ReturnType<typeof pageOf>>): FakeRoutes {
  return {
    'GET /v1/audit': (request) => {
      const params = new URL(request.url).searchParams
      return json(pages[`${params.get('action') ?? ''}|${params.get('cursor') ?? ''}`] ?? pageOf([]))
    },
  }
}

const paramsOf = (request: Request) => new URL(request.url).searchParams

/** A promise a test settles by hand, to hold an answer back. */
function gate() {
  let open!: () => void
  const closed = new Promise<void>((resolve) => {
    open = resolve
  })
  return { closed, open }
}

// Built with fromCodePoint on purpose: the characters under test must never sit in this file.
const RLO = String.fromCodePoint(0x202e)

describe('AuditPage: the table', () => {
  it('asks for the newest events, 50 at a time, with no filter and no cursor', async () => {
    const { fakeApi } = renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([makeEvent()]) }) })

    await screen.findByRole('table')

    const [params] = fakeApi.queries('GET /v1/audit')
    expect(params?.get('limit')).toBe(String(AUDIT_PAGE_SIZE))
    expect(params?.has('action')).toBe(false)
    expect(params?.has('cursor')).toBe(false)
  })

  it('shows each event with every column', async () => {
    const event = makeEvent()
    renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([event]) }) })

    const table = await screen.findByRole('table', { name: es.table.caption })
    const headers = [
      es.table.seq,
      es.table.at,
      es.table.actorType,
      es.table.actorId,
      es.table.action,
      es.table.entity,
      es.table.detail,
      es.table.hashes,
    ]
    for (const name of headers) expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument()

    const row = within(table).getAllByRole('row')[1]
    if (row === undefined) throw new Error('the table has no body row')
    const cells = within(row)
    expect(cells.getByText('7')).toBeInTheDocument()
    expect(cells.getByText(es.actorType.STAFF)).toBeInTheDocument()
    expect(cells.getByText('staff.created')).toBeInTheDocument()
    expect(cells.getByText('staff_user')).toBeInTheDocument()
    expect(cells.getByText('{"roles":["ANALYST"]}')).toBeInTheDocument()
    // Ids are mono and cut by CSS in the middle, so the random end stays visible; the whole
    // value is in the text and in the hover title.
    for (const id of [event.actor_id, event.entity_id ?? '']) {
      const shown = cells.getByTitle(id)
      expect(shown).toHaveClass('font-mono')
      expect(shown).toHaveTextContent(id)
      expect(shown.firstElementChild).toHaveClass('truncate')
      expect(shown.lastElementChild).toHaveTextContent(id.slice(-8))
    }
    // The pair of hashes: the first 8 hex characters of each, the whole hash on hover.
    expect(cells.getByText(es.hash.prevLabel)).toBeInTheDocument()
    expect(cells.getByText(es.hash.eventLabel)).toBeInTheDocument()
    expect(cells.getByText('a1a1a1a1')).toHaveAttribute('title', event.prev_hash)
    expect(cells.getByText('b2b2b2b2')).toHaveAttribute('title', event.hash)
  })

  it('writes the time in the viewer\'s zone, to the second, with the zone named', async () => {
    const event = makeEvent({ at: '2026-10-18T16:24:05.123456Z' })
    renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([event]) }) })

    const table = await screen.findByRole('table')
    const time = table.querySelector('time')
    expect(time).toHaveAttribute('datetime', '2026-10-18T16:24:05.123456Z')
    expect(time).toHaveAttribute('title', '2026-10-18T16:24:05.123456Z')
    // The date over "9:24:05 a.m. GMT-7": seconds, the 12-hour marker and a zone token.
    expect(time?.children[0]).toHaveTextContent(/^18 oct 2026$/)
    expect(time?.children[1]?.textContent).toMatch(/^\d{1,2}:24:05\s?[ap]\.\s?m\.\s+\S+$/i)
  })

  it('names unknown actor types as the API sent them', async () => {
    renderApp(<AuditPage />, {
      routes: auditList({ '|': pageOf([makeEvent({ actor_type: 'ROBOT' })]) }),
    })

    expect(await screen.findByText('ROBOT')).toBeInTheDocument()
  })

  it('shows events without an entity or detail as such', async () => {
    renderApp(<AuditPage />, {
      routes: auditList({ '|': pageOf([makeEvent({ entity_type: null, entity_id: null, detail: null })]) }),
    })

    const table = await screen.findByRole('table')
    expect(within(table).getByText(es.entity.none)).toBeInTheDocument()
    expect(within(table).getByText(es.detail.none)).toBeInTheDocument()
  })

  it('copies the whole hash from its button', async () => {
    const event = makeEvent({ seq: 7 })
    const { user } = renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([event]) }) })
    await screen.findByRole('table')

    await user.click(screen.getByRole('button', { name: es.hash.copyHash.replace('{{seq}}', '7') }))
    expect(await navigator.clipboard.readText()).toBe(event.hash)

    await user.click(screen.getByRole('button', { name: es.hash.copyPrev.replace('{{seq}}', '7') }))
    expect(await navigator.clipboard.readText()).toBe(event.prev_hash)
  })

  it('follows the language switcher', async () => {
    await i18n.changeLanguage('en')
    renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([makeEvent()]) }) })

    const table = await screen.findByRole('table', { name: en.audit.table.caption })
    expect(within(table).getByRole('columnheader', { name: en.audit.table.at })).toBeInTheDocument()
    expect(within(table).getByText(en.audit.actorType.STAFF)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: en.audit.verify.button })).toBeInTheDocument()
  })
})

describe('AuditPage: the detail', () => {
  const long = { note: 'n'.repeat(300), tail: 'END-OF-DETAIL' }

  it('shows a short detail whole and offers no expansion', async () => {
    renderApp(<AuditPage />, {
      routes: auditList({ '|': pageOf([makeEvent({ detail: { a: 1 } })]) }),
    })

    expect(await screen.findByText('{"a":1}')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: new RegExp(es.detail.expand) })).toBeNull()
  })

  it('cuts a long detail to about 120 characters and expands the whole thing in a sheet', async () => {
    const event = makeEvent({ seq: 7, detail: long })
    const { user } = renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([event]) }) })
    const table = await screen.findByRole('table')

    const preview = within(table).getByText(/^\{"note":"n+…$/)
    expect(preview.tagName).toBe('PRE')
    expect(preview.textContent).toHaveLength(121)
    expect(within(table).queryByText(/END-OF-DETAIL/)).toBeNull()

    const expand = within(table).getByRole('button', { name: es.detail.expandFor.replace('{{seq}}', '7') })
    expect(expand).toHaveTextContent(es.detail.expand)
    await user.click(expand)

    const sheet = await screen.findByRole('dialog', { name: es.detail.sheetTitle.replace('{{seq}}', '7') })
    const pretty = sheet.querySelector('pre')
    expect(pretty?.textContent).toBe(JSON.stringify(long, null, 2))
    expect(within(sheet).getByText(/staff\.created/)).toBeInTheDocument()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(expand).toHaveFocus() // the keyboard user lands where they were
  })

  it('copies the indented JSON from the sheet', async () => {
    const { user } = renderApp(<AuditPage />, {
      routes: auditList({ '|': pageOf([makeEvent({ detail: long })]) }),
    })
    await user.click(await screen.findByRole('button', { name: /^Ver detalle completo/ }))
    const sheet = await screen.findByRole('dialog')

    await user.click(within(sheet).getByRole('button', { name: es.detail.copy }))

    expect(await navigator.clipboard.readText()).toBe(JSON.stringify(long, null, 2))
  })
})

describe('AuditPage: nothing from the trail becomes markup', () => {
  const hostile = '<img src=x onerror=alert(1)>'

  it('renders a detail containing an img tag as the literal text, in the table and in the sheet', async () => {
    const event = makeEvent({ detail: { name: hostile, filler: 'f'.repeat(200) } })
    const { user, container } = renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([event]) }) })
    const table = await screen.findByRole('table')

    // In the table: text in a <pre>, no element made from it.
    const preview = table.querySelector('pre')
    expect(preview?.textContent).toContain(hostile)
    expect(container.querySelector('img')).toBeNull()
    expect(document.body.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;')

    // In the expanded sheet too.
    await user.click(screen.getByRole('button', { name: /^Ver detalle completo/ }))
    const sheet = await screen.findByRole('dialog')
    expect(sheet.querySelector('pre')?.textContent).toContain(hostile)
    expect(document.querySelector('img')).toBeNull()
  })

  it('prints every other field as text as well', async () => {
    const event = makeEvent({
      actor_id: '<script>alert(1)</script>',
      action: '<b>bold</b>',
      entity_type: '<i>kind</i>',
      entity_id: '<u onmouseover=alert(1)>id</u>',
      actor_type: '<svg onload=alert(1)>',
    })
    renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([event]) }) })
    const table = await screen.findByRole('table')

    for (const text of [event.action, event.entity_type, event.actor_type]) {
      expect(within(table).getByText(text ?? '')).toBeInTheDocument()
    }
    // Ids are cut in two pieces for display; the whole value is their hover title and text.
    for (const id of [event.actor_id, event.entity_id]) {
      expect(within(table).getByTitle(id ?? '')).toHaveTextContent(id ?? '')
    }
    expect(table.querySelector('script, b, i, u, svg[onload]')).toBeNull()
    for (const element of table.querySelectorAll('*')) {
      expect(element.getAttributeNames().filter((name) => name.startsWith('on'))).toEqual([])
    }
  })

  it('shows direction overrides and zero-width characters as visible escapes', async () => {
    const event = makeEvent({
      action: `auth.login${RLO}devil`,
      detail: { name: `a${RLO}b` },
    })
    renderApp(<AuditPage />, { routes: auditList({ '|': pageOf([event]) }) })
    const table = await screen.findByRole('table')

    expect(within(table).getByText('auth.login\\u202edevil')).toBeInTheDocument()
    expect(within(table).getByText('{"name":"a\\u202eb"}')).toBeInTheDocument()
    expect(table.textContent).not.toContain(RLO)
  })
})

describe('AuditPage: the action filter', () => {
  it('waits for typing to pause, then asks for the exact action', async () => {
    const { user, fakeApi } = renderApp(<AuditPage />, {
      routes: auditList({
        '|': pageOf(makeEvents(2, 2)),
        'staff.created|': pageOf([makeEvent({ seq: 9, id: '0191d3a2-6b1c-7000-8000-000000009999' })]),
      }),
    })
    await screen.findByRole('table')

    await user.type(screen.getByRole('textbox', { name: es.filter.label }), 'staff.created')

    expect(await screen.findByText('9')).toBeInTheDocument()
    const asked = fakeApi.queries('GET /v1/audit').map((params) => params.get('action'))
    expect(asked).toEqual([null, 'staff.created']) // not once per keystroke
  })

  it('applies the filter at once on Enter', async () => {
    const { user, fakeApi } = renderApp(<AuditPage />, {
      routes: auditList({ '|': pageOf(makeEvents(1, 1)), 'auth.login|': pageOf([]) }),
    })
    await screen.findByRole('table')

    await user.type(screen.getByRole('textbox', { name: es.filter.label }), 'auth.login{Enter}')

    await waitFor(() => expect(fakeApi.calls('GET /v1/audit')).toHaveLength(2))
    expect(fakeApi.queries('GET /v1/audit')[1]?.get('action')).toBe('auth.login')
  })

  it('never sends an empty or blank action, which the API would match against nothing', async () => {
    const { user, fakeApi } = renderApp(<AuditPage />, { routes: auditList({ '|': pageOf(makeEvents(1, 1)) }) })
    await screen.findByRole('table')

    await user.type(screen.getByRole('textbox', { name: es.filter.label }), '   ')
    await new Promise((resolve) => setTimeout(resolve, 450)) // past the debounce

    expect(fakeApi.calls('GET /v1/audit')).toHaveLength(1)
    expect(fakeApi.queries('GET /v1/audit').every((params) => !params.has('action'))).toBe(true)
  })

  it('trims the action it sends and limits it to what the API accepts', async () => {
    const { user, fakeApi } = renderApp(<AuditPage />, { routes: auditList({ '|': pageOf(makeEvents(1, 1)) }) })
    await screen.findByRole('table')
    const input = screen.getByRole('textbox', { name: es.filter.label })

    await user.type(input, '  api_key.created  {Enter}')

    await waitFor(() => expect(fakeApi.calls('GET /v1/audit')).toHaveLength(2))
    expect(fakeApi.queries('GET /v1/audit')[1]?.get('action')).toBe('api_key.created')
    expect(input).toHaveAttribute('maxlength', '120')
  })

  it('starts over from the newest event: a filter never travels with an old cursor', async () => {
    const { user, fakeApi } = renderApp(<AuditPage />, {
      routes: auditList({
        '|': pageOf(makeEvents(2, 4), 'c1'),
        '|c1': pageOf(makeEvents(2, 2)),
        // The filtered list has more than one page too, so the pager (and its page number) stays.
        'staff.created|': pageOf([makeEvent({ seq: 9, id: '0191d3a2-6b1c-7000-8000-000000009999' })], 'f1'),
      }),
    })
    await screen.findByText('Página 1')
    await user.click(screen.getByRole('button', { name: es.pager.next }))
    expect(await screen.findByText('Página 2')).toBeInTheDocument()
    await screen.findByText('1') // page two's last event (seq 1) has arrived

    await user.type(screen.getByRole('textbox', { name: es.filter.label }), 'staff.created{Enter}')

    expect(await screen.findByText('Página 1')).toBeInTheDocument()
    await screen.findByText('9')
    const filtered = fakeApi.queries('GET /v1/audit').filter((params) => params.has('action'))
    expect(filtered.length).toBeGreaterThan(0)
    expect(filtered.every((params) => !params.has('cursor'))).toBe(true)
  })

  it('clears the filter from its button and shows every event again', async () => {
    const { user } = renderApp(<AuditPage />, {
      routes: auditList({ '|': pageOf(makeEvents(2, 2)), 'x|': pageOf([]) }),
    })
    await screen.findByRole('table')
    const input = screen.getByRole('textbox', { name: es.filter.label })
    await user.type(input, 'x{Enter}')
    expect(await screen.findByText(es.empty.filtered.replace('{{action}}', 'x'))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: es.filter.clear }))

    expect(input).toHaveValue('')
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: es.filter.clear })).toBeNull()
  })
})

describe('AuditPage: paging', () => {
  it('shows no pager when everything fits on one page', async () => {
    renderApp(<AuditPage />, { routes: auditList({ '|': pageOf(makeEvents(3, 3)) }) })

    await screen.findByRole('table')

    expect(screen.queryByRole('navigation', { name: es.pager.label })).toBeNull()
  })

  const routes = auditList({
    '|': pageOf(makeEvents(2, 4), 'c1'),
    '|c1': pageOf(makeEvents(2, 2), 'c2'),
    '|c2': pageOf(makeEvents(1, 0)),
  })

  it('goes forward with the cursor the API handed out and back through the cursors it kept', async () => {
    const { user, fakeApi } = renderApp(<AuditPage />, { routes })
    const previous = () => screen.getByRole('button', { name: es.pager.previous })
    const next = () => screen.getByRole('button', { name: es.pager.next })
    await screen.findByText('Página 1')
    expect(previous()).toBeDisabled()
    expect(next()).toBeEnabled()

    await user.click(next())
    await screen.findByText('Página 2')
    await waitFor(() => expect(screen.getByText('2')).toBeInTheDocument()) // seq 2 of the second page
    expect(fakeApi.queries('GET /v1/audit').at(-1)?.get('cursor')).toBe('c1')
    expect(previous()).toBeEnabled()

    await user.click(next())
    await screen.findByText('Página 3')
    await waitFor(() => expect(screen.getByText('0')).toBeInTheDocument())
    expect(fakeApi.queries('GET /v1/audit').at(-1)?.get('cursor')).toBe('c2')
    expect(next()).toBeDisabled() // the last page has no next cursor

    await user.click(previous())
    await screen.findByText('Página 2')
    await user.click(previous())
    await screen.findByText('Página 1')
    await waitFor(() => expect(screen.getByText('4')).toBeInTheDocument())
    expect(previous()).toBeDisabled()
  })

  it('keeps both buttons still while a page loads, so a second click cannot act on a stale cursor', async () => {
    const pending = gate()
    const { user, fakeApi } = renderApp(<AuditPage />, {
      routes: {
        'GET /v1/audit': async (request) => {
          const cursor = paramsOf(request).get('cursor')
          if (cursor === 'c1') {
            await pending.closed
            return json(pageOf(makeEvents(2, 2), 'c2'))
          }
          return json(pageOf(makeEvents(2, 4), 'c1'))
        },
      },
    })
    await screen.findByText('Página 1')

    await user.click(screen.getByRole('button', { name: es.pager.next }))

    await screen.findByText('Página 2')
    expect(screen.getByRole('button', { name: es.pager.next })).toBeDisabled()
    expect(screen.getByRole('button', { name: es.pager.previous })).toBeDisabled()
    expect(screen.getByRole('table').closest('[aria-busy]')).toHaveAttribute('aria-busy', 'true')

    pending.open()
    await waitFor(() => expect(screen.getByRole('button', { name: es.pager.next })).toBeEnabled())
    expect(screen.getByRole('table').closest('[aria-busy]')).toHaveAttribute('aria-busy', 'false')
    expect(fakeApi.queries('GET /v1/audit').filter((params) => params.get('cursor') === 'c1')).toHaveLength(1)
  })
})

describe('AuditPage: paging when a page fails', () => {
  it('keeps Previous within reach, so a failing page does not strand the user', async () => {
    const { user } = renderApp(<AuditPage />, {
      routes: {
        'GET /v1/audit': (request) =>
          paramsOf(request).get('cursor') === 'c1' ? problem(500, 'INTERNAL') : json(pageOf(makeEvents(2, 4), 'c1')),
      },
    })
    await screen.findByText('Página 1')

    await user.click(screen.getByRole('button', { name: es.pager.next }))

    expect(await screen.findByRole('alert')).toHaveTextContent(esMX.errors.INTERNAL)
    expect(screen.getByText('Página 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.pager.previous })).toBeEnabled()
    expect(screen.getByRole('button', { name: es.pager.next })).toBeDisabled() // no cursor to follow from a failed page

    await user.click(screen.getByRole('button', { name: es.pager.previous }))

    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.getByText('Página 1')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows no pager when the first page fails: there is nowhere to go', async () => {
    renderApp(<AuditPage />, { routes: { 'GET /v1/audit': () => problem(500, 'INTERNAL') } })

    expect(await screen.findByRole('alert')).toHaveTextContent(esMX.errors.INTERNAL)
    expect(screen.queryByRole('navigation', { name: es.pager.label })).toBeNull()
  })
})

describe('AuditPage: loading, empty and failing', () => {
  it('shows a placeholder while the first page loads', async () => {
    const pending = gate()
    renderApp(<AuditPage />, {
      routes: {
        'GET /v1/audit': async () => {
          await pending.closed
          return json(pageOf([]))
        },
      },
    })

    expect(await screen.findByRole('status', { name: esMX.common.loading })).toBeInTheDocument()
    pending.open()
    expect(await screen.findByText(es.empty.none)).toBeInTheDocument()
  })

  it('says there are no events yet', async () => {
    renderApp(<AuditPage />, { routes: auditList({}) })

    expect(await screen.findByText(es.empty.none)).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('says which action matched nothing', async () => {
    const { user } = renderApp(<AuditPage />, { routes: auditList({ '|': pageOf(makeEvents(1, 1)) }) })
    await screen.findByRole('table')

    await user.type(screen.getByRole('textbox', { name: es.filter.label }), 'nope.nothing{Enter}')

    expect(await screen.findByText(es.empty.filtered.replace('{{action}}', 'nope.nothing'))).toBeInTheDocument()
  })

  it('explains a refusal in the user\'s language, quotes the request id, and retries', async () => {
    let allowed = false
    const { user } = renderApp(<AuditPage />, {
      routes: {
        'GET /v1/audit': () => (allowed ? json(pageOf([makeEvent()])) : problem(403, 'ROLE_FORBIDDEN')),
      },
    })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(esMX.errors.ROLE_FORBIDDEN)
    expect(alert).toHaveTextContent('req-test')
    expect(alert).not.toHaveTextContent('English detail')

    allowed = true
    await user.click(screen.getByRole('button', { name: esMX.common.retry }))

    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('refreshes the list on demand', async () => {
    const { user, fakeApi } = renderApp(<AuditPage />, { routes: auditList({ '|': pageOf(makeEvents(1, 1)) }) })
    await screen.findByRole('table')

    await user.click(screen.getByRole('button', { name: es.filter.refresh }))

    await waitFor(() => expect(fakeApi.calls('GET /v1/audit')).toHaveLength(2))
  })
})

describe('AuditPage: verifying the chain', () => {
  const verifyRoute = (body: ReturnType<typeof verification>) => ({
    ...auditList({ '|': pageOf(makeEvents(1, 1)) }),
    'GET /v1/audit/verify': () => json(body),
  })

  it('shows nothing about the chain until it is asked to verify', async () => {
    const { fakeApi } = renderApp(<AuditPage />, { routes: verifyRoute(verification()) })
    await screen.findByRole('table')

    expect(screen.queryByRole('region')).toBeNull()
    expect(fakeApi.calls('GET /v1/audit/verify')).toHaveLength(0)
  })

  it('reports an intact chain with the number of events walked', async () => {
    const { user } = renderApp(<AuditPage />, {
      routes: verifyRoute(verification({ valid: true, entries_checked: 1234 })),
    })

    await user.click(await screen.findByRole('button', { name: es.verify.button }))

    const card = await screen.findByRole('region', { name: es.verify.valid.title })
    expect(card).toHaveAttribute('data-outcome', 'valid')
    expect(within(card).getByText('1,234')).toBeInTheDocument()
  })

  it('does not call an empty chain intact', async () => {
    const { user } = renderApp(<AuditPage />, {
      routes: verifyRoute(verification({ valid: true, entries_checked: 0 })),
    })

    await user.click(await screen.findByRole('button', { name: es.verify.button }))

    const card = await screen.findByRole('region', { name: es.verify.empty.title })
    expect(card).toHaveAttribute('data-outcome', 'empty')
    expect(screen.queryByText(es.verify.valid.title)).toBeNull()
  })

  it('reports a broken chain with the first broken sequence number and the translated reason', async () => {
    const { user } = renderApp(<AuditPage />, {
      routes: verifyRoute(
        verification({
          valid: false,
          entries_checked: 42,
          first_broken_seq: 42,
          reason: 'prev_hash does not match the previous entry',
        }),
      ),
    })

    await user.click(await screen.findByRole('button', { name: es.verify.button }))

    const card = await screen.findByRole('region', { name: es.verify.broken.title })
    expect(card).toHaveAttribute('data-outcome', 'broken')
    expect(within(card).getByText(es.verify.broken.firstBrokenSeq)).toBeInTheDocument()
    expect(within(card).getAllByText('42').length).toBeGreaterThan(0)
    expect(within(card).getByText(es.verify.reason.prevHash)).toBeInTheDocument()
  })

  it('holds the button while verifying, forgets the old verdict, and shows the new one', async () => {
    const pending = gate()
    let calls = 0
    const { user } = renderApp(<AuditPage />, {
      routes: {
        ...auditList({ '|': pageOf(makeEvents(1, 1)) }),
        'GET /v1/audit/verify': async () => {
          calls += 1
          if (calls === 2) await pending.closed
          return json(calls === 1 ? verification({ entries_checked: 5 }) : verification({ valid: false, entries_checked: 9, first_broken_seq: 9, reason: 'content does not match its hash' }))
        },
      },
    })

    await user.click(await screen.findByRole('button', { name: es.verify.button }))
    await screen.findByRole('region', { name: es.verify.valid.title })

    await user.click(screen.getByRole('button', { name: es.verify.button }))
    const busy = await screen.findByRole('button', { name: es.verify.running })
    expect(busy).toBeDisabled()
    expect(screen.queryByRole('region')).toBeNull() // the old verdict is not left standing

    pending.open()
    expect(await screen.findByRole('region', { name: es.verify.broken.title })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.verify.button })).toBeEnabled()
  })

  it('shows a failed verification as an error, not as a verdict', async () => {
    const { user } = renderApp(<AuditPage />, {
      routes: {
        ...auditList({ '|': pageOf(makeEvents(1, 1)) }),
        'GET /v1/audit/verify': () => problem(500, 'INTERNAL'),
      },
    })

    await user.click(await screen.findByRole('button', { name: es.verify.button }))

    expect(await screen.findByText(esMX.errors.INTERNAL)).toBeInTheDocument()
    expect(screen.queryByRole('region')).toBeNull()
  })

  it('announces the verdict politely', async () => {
    const { user } = renderApp(<AuditPage />, { routes: verifyRoute(verification()) })

    await user.click(await screen.findByRole('button', { name: es.verify.button }))

    const card = await screen.findByRole('region', { name: es.verify.valid.title })
    expect(card.closest('[aria-live]')).toHaveAttribute('aria-live', 'polite')
  })
})

describe('AuditPage: who may open it', () => {
  /** The same nesting the app's router uses for /audit. */
  const guarded = (
    <Routes>
      <Route element={<RequireAuth />}>
        <Route element={<RequireRole roles={ACCESS.audit} />}>
          <Route path="/audit" element={<AuditPage />} />
        </Route>
      </Route>
    </Routes>
  )

  it.each<Role>(['MLRO', 'ADMIN', 'AUDITOR_RO'])('opens for %s', async (role) => {
    renderApp(guarded, {
      path: '/audit',
      me: makeMe({ roles: [role] }),
      routes: auditList({ '|': pageOf([makeEvent()]) }),
    })

    expect(await screen.findByRole('table')).toBeInTheDocument()
  })

  it.each<Role>(['ANALYST', 'SENIOR_ANALYST'])('is closed to %s, which never asks for the log', async (role) => {
    const { fakeApi } = renderApp(guarded, {
      path: '/audit',
      me: makeMe({ roles: [role] }),
      routes: auditList({ '|': pageOf([makeEvent()]) }),
    })

    expect(await screen.findByRole('heading', { name: esMX.forbidden.title })).toBeInTheDocument()
    expect(fakeApi.calls('GET /v1/audit')).toHaveLength(0)
    expect(screen.queryByRole('table')).toBeNull()
  })
})
