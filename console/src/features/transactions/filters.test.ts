import { describe, expect, it } from 'vitest'
import { filtersFromSearch, filtersToQuery, rangeIsValid, type TransactionFilters } from './filters'

describe('filtersToQuery', () => {
  it('drops empty values and keeps the rest as sent', () => {
    expect(
      filtersToQuery({
        party_id: ' 0191d3a2-6b1c-7000-8000-0000000000p1 ',
        external_id: '',
        from: '2026-10-01',
        to: undefined,
        status: 'PARKED',
        late: false,
      }),
    ).toEqual({ party_id: '0191d3a2-6b1c-7000-8000-0000000000p1', from: '2026-10-01', status: 'PARKED', late: false })
  })

  it('sends nothing for no filters', () => {
    expect(filtersToQuery({})).toEqual({})
    expect(filtersToQuery({ external_id: '   ', instrument: undefined })).toEqual({})
  })
})

describe('filtersFromSearch', () => {
  it('round trips through the query string', () => {
    const filters: TransactionFilters = {
      party_id: '0191d3a2-6b1c-7000-8000-0000000000b1',
      external_id: 'TX-1',
      from: '2026-10-01',
      to: '2026-10-31',
      status: 'PROCESSED',
      instrument: 'TRANSFER',
      rail: 'SPEI',
      late: true,
    }
    const search = new URLSearchParams(
      Object.entries(filtersToQuery(filters)).map(([key, value]) => [key, String(value)]),
    )

    expect(filtersFromSearch(search)).toEqual(filters)
  })

  it('ignores unknown keys, unknown enum values, empty values and malformed dates', () => {
    expect(
      filtersFromSearch(
        new URLSearchParams('status=NOPE&instrument=GOLD&rail=&from=01/10/2026&to=2026-13-45&late=maybe&other=1'),
      ),
    ).toEqual({})
  })

  it('drops a party id that is not a UUID, as the form refuses it', () => {
    expect(filtersFromSearch(new URLSearchParams('party_id=CLIENTE-1&status=PARKED'))).toEqual({ status: 'PARKED' })
  })

  it('reads late=false as a filter', () => {
    expect(filtersFromSearch(new URLSearchParams('late=false'))).toEqual({ late: false })
  })
})

describe('rangeIsValid', () => {
  it('accepts an open or ordered range and refuses a reversed one', () => {
    expect(rangeIsValid({})).toBe(true)
    expect(rangeIsValid({ from: '2026-10-01' })).toBe(true)
    expect(rangeIsValid({ to: '2026-10-01' })).toBe(true)
    expect(rangeIsValid({ from: '2026-10-01', to: '2026-10-01' })).toBe(true)
    expect(rangeIsValid({ from: '2026-10-02', to: '2026-10-01' })).toBe(false)
  })
})
