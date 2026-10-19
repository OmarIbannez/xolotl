import { afterEach, describe, expect, it, vi } from 'vitest'

// Zod compiles object parsers with `new Function` when the engine allows it, and finds out
// by trying `new Function('')` once. Under a Content-Security-Policy without 'unsafe-eval',
// which is how the API serves the console, the browser blocks that and reports a violation
// even though zod catches the error. These tests watch the constructor instead of a browser.

/** Load a fresh zod and count what is compiled while it builds and runs an object schema. */
async function compiledWhileParsing(configure: boolean): Promise<string[]> {
  vi.resetModules()
  const compiled: string[] = []
  vi.stubGlobal(
    'Function',
    new Proxy(Function, {
      construct(target, args: string[], newTarget) {
        compiled.push(args.join(','))
        return Reflect.construct(target, args, newTarget) as object
      },
    }),
  )
  if (configure) await import('./zod-config')
  const { z } = await import('zod')
  z.object({ name: z.string() }).parse({ name: 'ana' })
  return compiled
}

afterEach(async () => {
  vi.unstubAllGlobals()
  const { z } = await import('zod')
  z.config({ jitless: false }) // the flag lives on globalThis, shared by every copy of zod
})

describe('zod without the console configuration', () => {
  it('probes and compiles code, which a strict policy reports as a violation', async () => {
    const compiled = await compiledWhileParsing(false)

    expect(compiled).toContain('') // the probe: new Function('')
  })
})

describe('zod with the console configuration', () => {
  it('never asks the engine to compile code', async () => {
    const compiled = await compiledWhileParsing(true)

    expect(compiled).toEqual([])
  })

  it('still validates', async () => {
    await import('./zod-config')
    const { z } = await import('zod')
    const schema = z.object({ name: z.string().min(1) })

    expect(schema.safeParse({ name: 'ana' }).success).toBe(true)
    expect(schema.safeParse({ name: '' }).success).toBe(false)
  })
})
