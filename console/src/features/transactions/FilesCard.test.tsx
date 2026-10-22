// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FileStatusResponse, Role } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { FilesCard } from './FilesCard'
import { fileStatus } from './ingest-fixtures'
import { MAX_FILE_MB } from './upload'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.transactions.files
const UPLOAD = 'POST /v1/transactions/files'
const STATUS = 'GET /v1/transactions/files/f1'
const CSV = 'external_id,occurred_at,customer_ref,direction,rail,instrument,amount,currency\nx-1,2026-12-07T10:00:00Z,cust-1,IN,CASH,CASH,100.00,MXN\n'

function setup(routes: FakeRoutes = {}, roles: Role[] = ['ANALYST']) {
  return renderApp(<FilesCard pollMs={20} />, {
    path: '/transactions',
    me: makeMe({ roles }),
    routes: {
      [UPLOAD]: () => json(fileStatus(), { status: 202 }),
      [STATUS]: () => json(fileStatus({ status: 'COMPLETED', rows_total: 1, rows_accepted: 1 })),
      ...routes,
    },
  })
}

async function openDialog(user: ReturnType<typeof setup>['user']) {
  await user.click(await screen.findByRole('button', { name: text.upload }))
  return screen.findByRole('dialog')
}

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.info).mockClear()
})

describe('FilesCard', () => {
  it('links the template from the upload dialog', async () => {
    const { user } = setup()

    const dialog = await openDialog(user)

    expect(within(dialog).getByRole('link', { name: text.template }).getAttribute('href')).toBe('/plantilla-transacciones.csv')
  })

  it('sends the file bytes as the raw body with their content type and an Idempotency-Key', async () => {
    const { user, fakeApi } = setup()
    const dialog = await openDialog(user)

    await user.upload(within(dialog).getByLabelText(text.file), new File([CSV], 'movs.csv', { type: '' }))
    await user.click(within(dialog).getByRole('button', { name: text.submit }))

    await waitFor(() => expect(fakeApi.calls(UPLOAD)).toHaveLength(1))
    const [request] = fakeApi.calls(UPLOAD)
    expect(request?.headers.get('Content-Type')).toBe('text/csv')
    expect(request?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await request?.text()).toBe(CSV)
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.uploaded))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says so when the same bytes were uploaded before (200)', async () => {
    const { user } = setup({ [UPLOAD]: () => json(fileStatus({ status: 'COMPLETED', rows_total: 1, rows_accepted: 1 })) })
    const dialog = await openDialog(user)

    await user.upload(within(dialog).getByLabelText(text.file), new File([CSV], 'movs.csv', { type: 'text/csv' }))
    await user.click(within(dialog).getByRole('button', { name: text.submit }))

    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(text.known))
    expect(await screen.findByText(text.fileStatus.COMPLETED)).toBeTruthy()
  })

  it('refuses a file over 50 MB before any request', async () => {
    const { user, fakeApi } = setup()
    const dialog = await openDialog(user)
    const big = new File(['x'], 'big.csv', { type: 'text/csv' })
    Object.defineProperty(big, 'size', { value: MAX_FILE_MB * 1024 * 1024 + 1 })

    await user.upload(within(dialog).getByLabelText(text.file), big)
    await user.click(within(dialog).getByRole('button', { name: text.submit }))

    expect((await within(dialog).findByRole('alert')).textContent).toBe(text.tooLarge)
    expect(fakeApi.calls(UPLOAD)).toEqual([])
  })

  it('refuses a file that is neither CSV, JSON Lines nor XLSX', async () => {
    const { user, fakeApi } = setup()
    const dialog = await openDialog(user)

    // The picker's filter is only a hint: a user can still choose any file.
    fireEvent.change(within(dialog).getByLabelText(text.file), {
      target: { files: [new File(['%PDF'], 'movs.pdf', { type: 'application/pdf' })] },
    })
    await user.click(within(dialog).getByRole('button', { name: text.submit }))

    expect((await within(dialog).findByRole('alert')).textContent).toBe(text.unsupported)
    expect(fakeApi.calls(UPLOAD)).toEqual([])
  })

  it.each([
    [422, 'TRANSACTION_FILE_INVALID'],
    [413, 'TRANSACTION_FILE_TOO_LARGE'],
  ] as const)('shows a %i as its translated error', async (status, code) => {
    const { user } = setup({ [UPLOAD]: () => problem(status, code) })
    const dialog = await openDialog(user)

    await user.upload(within(dialog).getByLabelText(text.file), new File([CSV], 'movs.csv', { type: 'text/csv' }))
    await user.click(within(dialog).getByRole('button', { name: text.submit }))

    expect(await within(dialog).findByText(esMX.errors[code])).toBeTruthy()
    expect(document.body.textContent).not.toContain(`English detail of ${code}`)
  })

  it('reads the status again while the file is processing and stops once it is completed', async () => {
    const answers: FileStatusResponse[] = [
      fileStatus({ status: 'PROCESSING', rows_total: 3, rows_accepted: 1 }),
      fileStatus({ status: 'COMPLETED', rows_total: 3, rows_accepted: 3, finished_at: '2026-12-07T15:02:00Z' }),
    ]
    const { user, fakeApi } = setup({
      [STATUS]: () => json(answers.shift() ?? fileStatus({ status: 'COMPLETED', rows_total: 3, rows_accepted: 3 })),
    })
    const dialog = await openDialog(user)
    await user.upload(within(dialog).getByLabelText(text.file), new File([CSV], 'movs.csv', { type: 'text/csv' }))
    await user.click(within(dialog).getByRole('button', { name: text.submit }))

    const card = await screen.findByRole('region', { name: text.statusLabel })
    expect(await within(card).findByText(text.fileStatus.COMPLETED)).toBeTruthy()
    const reads = fakeApi.calls(STATUS).length
    expect(reads).toBeGreaterThanOrEqual(2)
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(fakeApi.calls(STATUS)).toHaveLength(reads)
    expect(within(card).queryByRole('button', { name: text.downloadErrors })).toBeNull()
  })

  it('offers the error report only when rows were rejected', async () => {
    const { user } = setup({
      [STATUS]: () => json(fileStatus({ status: 'COMPLETED', rows_total: 3, rows_accepted: 2, rows_rejected: 1 })),
    })
    const dialog = await openDialog(user)
    await user.upload(within(dialog).getByLabelText(text.file), new File([CSV], 'movs.csv', { type: 'text/csv' }))
    await user.click(within(dialog).getByRole('button', { name: text.submit }))

    const card = await screen.findByRole('region', { name: text.statusLabel })
    expect(await within(card).findByRole('button', { name: text.downloadErrors })).toBeTruthy()
  })

  it('hides the upload from AUDITOR_RO, who cannot write', async () => {
    setup({}, ['AUDITOR_RO'])

    expect(await screen.findByText(text.title)).toBeTruthy()
    expect(screen.queryByRole('button', { name: text.upload })).toBeNull()
  })
})
