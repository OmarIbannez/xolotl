// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { json, problem } from '@/test/fake-network'
import { renderApp } from '@/test/render-app'
import { BatchesCard } from './BatchesCard'
import { transactionBatch } from './ingest-fixtures'

const text = esMX.transactions.batches
const BATCH_ID = '0191d3a2-6b1c-7000-8000-0000000000b1'
const BATCH = `GET /v1/transactions/batches/${BATCH_ID}`

describe('BatchesCard', () => {
  it('looks a batch up by id and shows its counts', async () => {
    const { user, fakeApi } = renderApp(<BatchesCard />, {
      path: '/transactions',
      routes: { [BATCH]: () => json(transactionBatch()) },
    })

    await user.type(await screen.findByLabelText(text.batchId), `  ${BATCH_ID} `)
    await user.click(screen.getByRole('button', { name: text.lookup }))

    expect(await screen.findByText(text.sourceKind.API)).toBeTruthy()
    expect(screen.getByText('12')).toBeTruthy()
    expect(screen.getByText('10')).toBeTruthy()
    expect(screen.getByText('2')).toBeTruthy()
    expect(fakeApi.calls(BATCH)).toHaveLength(1)
  })

  it('checks the id before asking', async () => {
    const { user, fakeApi } = renderApp(<BatchesCard />, { path: '/transactions' })

    await user.type(await screen.findByLabelText(text.batchId), 'lote-7')
    await user.click(screen.getByRole('button', { name: text.lookup }))

    expect(await screen.findByText(esMX.validation.uuid)).toBeTruthy()
    expect(fakeApi.requests.filter((request) => request.url.includes('/batches/'))).toEqual([])
  })

  it('says so when no batch has that id', async () => {
    const { user } = renderApp(<BatchesCard />, {
      path: '/transactions',
      routes: { [BATCH]: () => problem(404, 'NOT_FOUND') },
    })

    await user.type(await screen.findByLabelText(text.batchId), BATCH_ID)
    await user.click(screen.getByRole('button', { name: text.lookup }))

    await waitFor(() => expect(screen.getByText(text.notFound)).toBeTruthy())
  })
})
