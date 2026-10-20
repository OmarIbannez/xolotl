// @vitest-environment jsdom
import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { EvaluationReport } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json, problem, type FakeRoutes } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { EvaluationPage } from './EvaluationPage'

const text = esMX.screening.evaluation
const EVALUATION = 'GET /v1/screening/evaluation'

const REPORT: EvaluationReport = {
  matcher_version: '2.1.0',
  generated_at: '2026-10-20T16:55:38Z',
  corpus_pairs: 20294,
  hard_negatives: 520,
  alerts_per_1000_negatives: '0.0000',
  kinds: [
    {
      list_kind: 'FISCAL',
      pairs: 2666,
      recall_review: '0.9839',
      precision_alert: null,
      retrieval_recall: '1.0000',
    },
    {
      list_kind: 'SANCTION',
      pairs: 17628,
      recall_review: '0.9862',
      precision_alert: '0.9855',
      retrieval_recall: '0.9978',
    },
  ],
  floors: [
    { metric: 'recall', list_kind: 'SANCTION', floor: '0.9800', value: '0.9862', met: true },
    { metric: 'retrieval_recall', list_kind: 'SANCTION', floor: '0.9950', value: '0.9900', met: false },
  ],
}

function setup(routes: FakeRoutes = {}) {
  return renderApp(<EvaluationPage />, {
    path: '/screening/evaluation',
    me: makeMe({ roles: ['AUDITOR_RO'] }),
    routes: { [EVALUATION]: () => json(REPORT), ...routes },
  })
}

describe('EvaluationPage', () => {
  it('renders the figures per list kind and a met and a below floor', async () => {
    setup()

    const kinds = await screen.findByRole('table', { name: text.kinds.title })
    const sanctions = within(within(kinds).getByRole('row', { name: new RegExp(text.kind.SANCTION) }))
    expect(sanctions.getByText('17,628')).toBeTruthy()
    expect(sanctions.getByText('0.9862')).toBeTruthy()
    expect(sanctions.getByText('0.9855')).toBeTruthy()
    expect(sanctions.getByText('0.9978')).toBeTruthy()
    const fiscal = within(within(kinds).getByRole('row', { name: new RegExp(text.kind.FISCAL.replace(/[()]/g, '\\$&')) }))
    expect(fiscal.getByText('—')).toBeTruthy()

    const floors = screen.getByRole('table', { name: text.floors.title })
    const recall = within(within(floors).getByRole('row', { name: new RegExp(text.metric.recall.replace(/[()]/g, '\\$&')) }))
    expect(recall.getByText(text.floors.met)).toBeTruthy()
    expect(recall.getByText('0.9800')).toBeTruthy()
    const retrieval = within(within(floors).getByRole('row', { name: new RegExp(text.metric.retrieval_recall) }))
    expect(retrieval.getByText(text.floors.below)).toBeTruthy()
    expect(retrieval.getByText('0.9900')).toBeTruthy()
  })

  it('shows the matcher version and the generation date', async () => {
    setup()

    expect(await screen.findByText('2.1.0')).toBeTruthy()
    const generated = screen.getByText((_, element) => element?.getAttribute('datetime') === REPORT.generated_at)
    expect(generated.tagName).toBe('TIME')
    expect(generated.textContent).toMatch(/2026/)
    expect(screen.getByText('20,294')).toBeTruthy()
  })

  it('shows the empty state when the build ships no report', async () => {
    setup({ [EVALUATION]: () => problem(404, 'NOT_FOUND') })

    expect(await screen.findByText(text.empty)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
