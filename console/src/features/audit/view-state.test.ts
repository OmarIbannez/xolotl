import { describe, expect, it } from 'vitest'
import {
  auditViewReducer,
  currentCursor,
  FIRST_PAGE,
  pageNumber,
  type AuditView,
  type AuditViewEvent,
} from './view-state'

function run(...events: AuditViewEvent[]): AuditView {
  return events.reduce(auditViewReducer, FIRST_PAGE)
}

describe('audit view state', () => {
  it('starts on the first page of every event', () => {
    expect(FIRST_PAGE).toEqual({ action: '', cursors: [undefined] })
    expect(currentCursor(FIRST_PAGE)).toBeUndefined()
    expect(pageNumber(FIRST_PAGE)).toBe(1)
  })

  it('goes forward with the cursor the API handed out, and back one page at a time', () => {
    const second = run({ type: 'next', cursor: 'c1' })
    expect(currentCursor(second)).toBe('c1')
    expect(pageNumber(second)).toBe(2)

    const third = auditViewReducer(second, { type: 'next', cursor: 'c2' })
    expect(currentCursor(third)).toBe('c2')
    expect(pageNumber(third)).toBe(3)

    expect(currentCursor(auditViewReducer(third, { type: 'back' }))).toBe('c1')
    expect(currentCursor(run({ type: 'next', cursor: 'c1' }, { type: 'back' }))).toBeUndefined()
  })

  it('cannot go back from the first page', () => {
    expect(run({ type: 'back' })).toBe(FIRST_PAGE)
  })

  it('ignores a second click on "next" that carries the cursor already opened', () => {
    const once = run({ type: 'next', cursor: 'c1' })
    expect(auditViewReducer(once, { type: 'next', cursor: 'c1' })).toBe(once)
  })

  it('resets the cursor when the filter changes, so the new list starts at its newest event', () => {
    const deep = run({ type: 'next', cursor: 'c1' }, { type: 'next', cursor: 'c2' })
    const filtered = auditViewReducer(deep, { type: 'filter', action: 'auth.login' })
    expect(filtered).toEqual({ action: 'auth.login', cursors: [undefined] })
    expect(pageNumber(filtered)).toBe(1)
  })

  it('keeps the page when the filter is set to what it already is', () => {
    const deep = run({ type: 'filter', action: 'auth.login' }, { type: 'next', cursor: 'c1' })
    expect(auditViewReducer(deep, { type: 'filter', action: 'auth.login' })).toBe(deep)
  })

  it('goes back to every event when the filter is cleared, also from the first page', () => {
    const filtered = run({ type: 'filter', action: 'auth.login' })
    expect(auditViewReducer(filtered, { type: 'filter', action: '' })).toEqual(FIRST_PAGE)
  })
})
