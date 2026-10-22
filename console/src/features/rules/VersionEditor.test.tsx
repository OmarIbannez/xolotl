// @vitest-environment jsdom
import { screen, waitFor } from '@testing-library/react'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VersionResponse } from '@/api/types'
import esMX from '@/i18n/es-MX.json'
import { json } from '@/test/fake-network'
import { makeMe, renderApp } from '@/test/render-app'
import { chooseOption, stubPointerCapture } from '@/test/select'
import { RULE_ID, version } from './fixtures'
import { VersionEditor } from './VersionEditor'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const text = esMX.rules.editor
const PATCH = `PATCH /v1/monitoring/rules/${RULE_ID}/versions/1`

function setup(shown: VersionResponse = version()) {
  return renderApp(<VersionEditor ruleId={RULE_ID} version={shown} />, {
    me: makeMe({ roles: ['SENIOR_ANALYST'] }),
    routes: { [PATCH]: () => json(shown) },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  stubPointerCapture()
})

describe('VersionEditor', () => {
  it('sends every value typed as the API reads it: decimals as strings, integers as numbers', async () => {
    const { user, fakeApi } = setup()

    const ratio = screen.getByLabelText('near_ratio')
    await user.clear(ratio)
    await user.type(ratio, '0.80')
    const count = screen.getByLabelText('min_count')
    await user.clear(count)
    await user.type(count, '4')
    await user.type(screen.getByLabelText(text.segmentField.tier), 'n1, n2')
    await chooseOption(user, screen.getByLabelText(text.severity), esMX.hits.severity.HIGH)
    await chooseOption(user, screen.getByLabelText(text.dedupKey), esMX.rules.keyKind.PARTY)
    const cooldown = screen.getByLabelText(text.cooldown)
    await user.clear(cooldown)
    await user.type(cooldown, 'P14D')
    await user.click(screen.getByRole('button', { name: text.save }))

    await waitFor(() => expect(fakeApi.calls(PATCH)).toHaveLength(1))
    expect(await fakeApi.calls(PATCH)[0]?.json()).toEqual({
      params: { threshold: '5000', near_ratio: '0.80', window: 'P7D', min_count: 4 },
      segment: { tier: ['N1', 'N2'], risk_rating: [], monitoring_profile: [] },
      severity: 'HIGH',
      dedup: { key: 'PARTY', window: 'P7D', cooldown: 'P14D' },
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(text.saved))
  })

  it('refuses a decimal comma before anything is sent', async () => {
    const { user, fakeApi } = setup()

    const ratio = screen.getByLabelText('near_ratio')
    await user.clear(ratio)
    await user.type(ratio, '0,80')
    await user.click(screen.getByRole('button', { name: text.save }))

    expect(await screen.findByText(text.invalid.DECIMAL)).toBeTruthy()
    expect(ratio.getAttribute('aria-invalid')).toBe('true')
    expect(fakeApi.calls(PATCH)).toHaveLength(0)
  })

  it('offers a select for choices and a switch for a boolean', async () => {
    const { user, fakeApi } = setup(
      version({
        params: [
          { name: 'outcome', value: 'BLOCK', type: 'STR', min: null, max: null, choices: ['BLOCK', 'REVIEW'], tighten: null, unit: null, source: 'template TIER_N1_MONTHLY_OPS v1' },
          { name: 'self_custody', value: 'true', type: 'BOOL', min: null, max: null, choices: null, tighten: null, unit: null, source: 'template TIER_N1_MONTHLY_OPS v1' },
        ],
      }),
    )

    expect(screen.getByText('valores: BLOCK, REVIEW · límites de template TIER_N1_MONTHLY_OPS v1')).toBeTruthy()
    await chooseOption(user, screen.getByLabelText('outcome'), 'REVIEW')
    const toggle = screen.getByRole('switch', { name: 'self_custody' })
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    await user.click(toggle)
    await user.click(screen.getByRole('button', { name: text.save }))

    await waitFor(() => expect(fakeApi.calls(PATCH)).toHaveLength(1))
    const body = (await fakeApi.calls(PATCH)[0]?.json()) as { params: unknown; segment: unknown }
    expect(body.params).toEqual({ outcome: 'REVIEW', self_custody: false })
    expect(body.segment).toBeNull()
  })

  it('is read only without EDIT, and says why', () => {
    setup(version({ status: 'IN_REVIEW', allowed_actions: [], blocked_reasons: { EDIT: ['STATE'] } }))

    expect(screen.getByText(text.readOnly)).toBeTruthy()
    expect(screen.getByText(esMX.rules.blocked.STATE)).toBeTruthy()
    expect(screen.getByLabelText('threshold (USD)')).toBeDisabled()
    expect(screen.queryByRole('button', { name: text.save })).toBeNull()
  })
})
