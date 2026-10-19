import { describe, expect, it } from 'vitest'
import { DETAIL_PREVIEW_CHARS, prettyDetail, previewDetail } from './detail'

// Built with fromCodePoint on purpose: the characters under test must never sit in this file.
const RLO = String.fromCodePoint(0x202e)
const ZWSP = String.fromCodePoint(0x200b)

describe('previewDetail', () => {
  it('has nothing to show for an event without detail', () => {
    expect(previewDetail(null)).toBeNull()
  })

  it('shows a short detail whole, as one line of JSON', () => {
    expect(previewDetail({ role: 'ADMIN', n: 2 })).toEqual({ text: '{"role":"ADMIN","n":2}', truncated: false })
    expect(previewDetail({})).toEqual({ text: '{}', truncated: false })
  })

  it('shows exactly the limit whole and cuts one character past it', () => {
    const exactly = { k: 'x'.repeat(DETAIL_PREVIEW_CHARS - '{"k":""}'.length) }
    expect(JSON.stringify(exactly)).toHaveLength(DETAIL_PREVIEW_CHARS)
    expect(previewDetail(exactly)?.truncated).toBe(false)

    const over = { k: 'x'.repeat(DETAIL_PREVIEW_CHARS - '{"k":""}'.length + 1) }
    const preview = previewDetail(over)
    expect(preview?.truncated).toBe(true)
    expect(preview?.text).toBe(`${JSON.stringify(over).slice(0, DETAIL_PREVIEW_CHARS)}…`)
  })

  it('truncates a long detail to about 120 characters', () => {
    const preview = previewDetail({ note: 'y'.repeat(5000) })
    expect(preview?.truncated).toBe(true)
    expect(preview?.text).toHaveLength(DETAIL_PREVIEW_CHARS + 1)
    expect(preview?.text.endsWith('…')).toBe(true)
  })

  it('counts code points, so an emoji at the cut is never split into a broken half', () => {
    const text = previewDetail({ k: '😀'.repeat(200) })?.text ?? ''
    expect(text.endsWith('…')).toBe(true)
    expect(text).not.toMatch(/[\ud800-\udbff](?![\udc00-\udfff])/) // no lone high surrogate
    expect(text).not.toMatch(/(?<![\ud800-\udbff])[\udc00-\udfff]/) // no lone low surrogate
    expect(Array.from(text)).toHaveLength(DETAIL_PREVIEW_CHARS + 1)
  })

  it('keeps markup as text: the preview is a plain string for the page to print', () => {
    const preview = previewDetail({ note: '<img src=x onerror=alert(1)>' })
    expect(preview?.text).toBe('{"note":"<img src=x onerror=alert(1)>"}')
  })

  it('makes invisible characters visible', () => {
    const text = previewDetail({ name: `a${RLO}b` })?.text
    expect(text).toBe('{"name":"a\\u202eb"}')
    expect(text).not.toContain(RLO)
  })
})

describe('prettyDetail', () => {
  it('indents the whole detail', () => {
    expect(prettyDetail({ a: 1, nested: { b: [true, null] } })).toBe(
      ['{', '  "a": 1,', '  "nested": {', '    "b": [', '      true,', '      null', '    ]', '  }', '}'].join('\n'),
    )
  })

  it('is not truncated, however long', () => {
    const long = 'z'.repeat(10_000)
    expect(prettyDetail({ k: long })).toContain(long)
  })

  it('makes invisible characters visible here too', () => {
    const text = prettyDetail({ k: `a${ZWSP}b` })
    expect(text).not.toContain(ZWSP)
    expect(text).toContain('a\\u200bb')
  })
})
