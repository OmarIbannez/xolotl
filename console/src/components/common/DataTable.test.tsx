// @vitest-environment jsdom
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import esMX from '@/i18n/es-MX.json'
import { DataTable, type Column } from './DataTable'

interface Pet {
  id: string
  name: string
  retired: boolean
}

const columns: Column<Pet>[] = [
  { id: 'name', header: 'Nombre', cell: (pet) => <strong>{pet.name}</strong> },
  { id: 'retired', header: 'Retirado', cell: (pet) => (pet.retired ? 'sí' : 'no'), className: 'text-right' },
]

const rows: Pet[] = [
  { id: 'a', name: 'Axl', retired: false },
  { id: 'b', name: 'Bruno', retired: true },
]

const base = { label: 'Mascotas', columns, getRowId: (pet: Pet) => pet.id, empty: 'Sin mascotas' }

/** The nth row of the body (the header row is not counted). */
function bodyRow(index: number): HTMLElement {
  const row = screen.getAllByRole('row')[index + 1]
  if (!row) throw new Error(`no body row ${index}`)
  return row
}

describe('DataTable', () => {
  it('renders a header cell per column and a row per item, with the column cell renderers', () => {
    render(<DataTable {...base} rows={rows} />)

    const table = screen.getByRole('table', { name: 'Mascotas' })
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Nombre', 'Retirado'])
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 2)
    expect(within(bodyRow(0)).getByText('Axl').tagName).toBe('STRONG')
    expect(within(bodyRow(1)).getByText('Bruno')).toBeTruthy()
    expect(within(bodyRow(1)).getByText('sí')).toBeTruthy()
  })

  it('applies the column classes to its cells and the row class to the row', () => {
    render(<DataTable {...base} rows={rows} rowClassName={(pet) => (pet.retired ? 'muted-row' : undefined)} />)

    expect(bodyRow(0).className).not.toContain('muted-row')
    expect(bodyRow(1).className).toContain('muted-row')
    expect(within(bodyRow(1)).getByText('sí').className).toContain('text-right')
  })

  it('shows the header over placeholder rows, announced as loading, while loading', () => {
    render(<DataTable {...base} rows={undefined} loading loadingRows={3} />)

    const table = screen.getByRole('table', { name: 'Mascotas' })
    expect(table.getAttribute('aria-busy')).toBe('true')
    expect(within(table).getAllByRole('columnheader')).toHaveLength(2)
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 3)
    expect(screen.getByRole('status').textContent).toBe(esMX.common.loading)
    expect(screen.queryByText('Axl')).toBeNull()
  })

  it('treats missing rows as still loading, even without the flag', () => {
    render(<DataTable {...base} rows={undefined} />)

    expect(screen.getByRole('table').getAttribute('aria-busy')).toBe('true')
  })

  it('shows the empty notice instead of a table once it loaded with nothing', () => {
    render(<DataTable {...base} rows={[]} />)

    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByText('Sin mascotas')).toBeTruthy()
  })

  it('does not show the empty notice while rows are still loading', () => {
    render(<DataTable {...base} rows={[]} loading />)

    expect(screen.queryByText('Sin mascotas')).toBeNull()
    expect(screen.getByRole('table')).toBeTruthy()
  })
})
