// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeUser, renderWithProviders } from '@/test/render'
import { makeMe, renderApp } from '@/test/render-app'
import { stubPointerCapture } from '@/test/select'
import { DocumentsCard } from './DocumentsCard'
import { documentRow } from './fixtures'
import { MAX_DOCUMENT_BYTES } from './sub-forms'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.parties.documents
const UPLOAD = 'POST /v1/parties/p1/documents'
const CONTENT = 'GET /v1/parties/p1/documents/d1/content'

function setup(roles: Role[] = ['ANALYST'], routes: FakeRoutes = {}) {
  return renderApp(<DocumentsCard partyId="p1" documents={[documentRow()]} />, {
    me: makeMe({ roles }),
    routes: {
      [UPLOAD]: () => json(documentRow({ id: 'd2' }), { status: 201 }),
      [CONTENT]: () => new Response('%PDF-1.7', { headers: { 'content-type': 'application/pdf' } }),
      ...routes,
    },
  })
}

const fileInput = () => screen.findByLabelText(text.file)

beforeEach(() => {
  stubPointerCapture()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('DocumentsCard', () => {
  it('lists the documents with their type, size and a download', async () => {
    setup()

    expect(await screen.findByText(esMX.parties.documents.type.PROOF_OF_ADDRESS)).toBeTruthy()
    expect(screen.getByText('123,456')).toBeTruthy()
    expect(screen.getByRole('button', { name: text.download })).toBeTruthy()
  })

  it('sends a chosen 3-byte PDF as the raw body, typed, with doc_type in the query', async () => {
    const { user, fakeApi } = setup()
    const pdf = new File(['%PD'], 'acta.pdf', { type: 'application/pdf' })

    await user.upload(await fileInput(), pdf)
    await user.click(screen.getByRole('button', { name: text.upload }))

    await waitFor(() => expect(fakeApi.calls(UPLOAD)).toHaveLength(1))
    const [request] = fakeApi.calls(UPLOAD)
    expect(request?.headers.get('Content-Type')).toBe('application/pdf')
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await request?.text()).toBe('%PD')
    expect(fakeApi.queries(UPLOAD)[0]?.get('doc_type')).toBe('IDENTITY')
  })

  it('never sends a file over the limit, and says why', async () => {
    const { user, fakeApi } = setup()
    const big = new File(['x'], 'big.pdf', { type: 'application/pdf' })
    Object.defineProperty(big, 'size', { value: MAX_DOCUMENT_BYTES + 1 })

    await user.upload(await fileInput(), big)
    await user.click(screen.getByRole('button', { name: text.upload }))

    expect((await screen.findByRole('alert')).textContent).toBe(esMX.validation.fileTooLarge)
    expect(fakeApi.calls(UPLOAD)).toEqual([])
  })

  it('never sends a file of another type', async () => {
    const { user, fakeApi } = setup()
    const gif = new File(['GIF'], 'x.gif', { type: 'image/gif' })

    await userUploadIgnoringAccept(user, await fileInput(), gif)
    await user.click(screen.getByRole('button', { name: text.upload }))

    expect((await screen.findByRole('alert')).textContent).toBe(esMX.validation.fileType)
    expect(fakeApi.calls(UPLOAD)).toEqual([])
  })

  it('shows a refused upload', async () => {
    const { user } = setup(['ANALYST'], { [UPLOAD]: () => problem(422, 'VALIDATION_FAILED') })

    await user.upload(await fileInput(), new File(['%PD'], 'a.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: text.upload }))

    expect((await screen.findByRole('alert')).textContent).toContain(esMX.errors.VALIDATION_FAILED)
  })

  it('downloads the content on a click, saved under the document id', async () => {
    const createObjectURL = vi.fn(() => 'blob:doc')
    const revokeObjectURL = vi.fn()
    Object.assign(URL, { createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('d1.pdf')
      expect(this.href).toBe('blob:doc')
    })
    const { user, fakeApi } = setup()

    await user.click(await screen.findByRole('button', { name: text.download }))

    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:doc'))
    expect(fakeApi.calls(CONTENT)).toHaveLength(1)
    expect(click).toHaveBeenCalledTimes(1)
  })

  it('offers no upload to AUDITOR_RO, who can still download', async () => {
    // The profile is there from the first render, so what is missing is missing for the role.
    renderWithProviders(<DocumentsCard partyId="p1" documents={[documentRow()]} />, {
      user: makeUser({ roles: ['AUDITOR_RO'] }),
    })

    expect(screen.getByRole('button', { name: text.download })).toBeTruthy()
    expect(screen.queryByLabelText(text.file)).toBeNull()
    expect(screen.queryByRole('button', { name: text.upload })).toBeNull()
  })
})

/** `user.upload` drops files the input's `accept` excludes; this one is for the client-side check. */
async function userUploadIgnoringAccept(
  user: ReturnType<typeof renderApp>['user'],
  input: HTMLElement,
  file: File,
): Promise<void> {
  input.removeAttribute('accept')
  await user.upload(input, file)
}
