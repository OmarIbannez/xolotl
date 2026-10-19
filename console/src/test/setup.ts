/**
 * Vitest setup, run before every test file. Logic tests (*.test.ts: the API client, the session
 * store, routing rules, catalogues) run in plain Node and need none of it. A test that renders
 * components opts into jsdom with a first-line `// @vitest-environment jsdom` comment and gets:
 *  - React told it is under test, Testing Library's unmount between tests and a longer async
 *    timeout, because pages render whole trees (Radix dialogs, tables);
 *  - the jest-dom matchers;
 *  - the scriptable API in `./fake-network`, checked after each test for requests nothing answered;
 *  - stubs for ResizeObserver and matchMedia, which jsdom lacks;
 *  - the Spanish catalogue, the product default, so assertions read like the screen.
 */
import { afterEach, beforeEach, expect } from 'vitest'

if (typeof document !== 'undefined') {
  await import('@testing-library/jest-dom/vitest')
  const { cleanup, configure } = await import('@testing-library/react')
  const { resetFakeApi } = await import('./fake-network')
  const { session } = await import('@/auth/session')
  const { default: i18n } = await import('@/i18n')

  Reflect.set(globalThis, 'IS_REACT_ACT_ENVIRONMENT', true)
  configure({ asyncUtilTimeout: 4000 })

  // jsdom has no ResizeObserver, which Radix's checkbox uses when it sits inside a form.
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver ??= ResizeObserverStub

  // jsdom has no matchMedia, which the theme toggle reads.
  if (typeof window.matchMedia !== 'function') {
    window.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    })
  }

  await i18n.changeLanguage('es-MX')
  beforeEach(async () => {
    await i18n.changeLanguage('es-MX')
  })

  afterEach(async () => {
    cleanup() // unmount first, so nothing is left asking the API for more
    session.end()
    expect(resetFakeApi(), 'requests that no fake route answered').toEqual([])
    await i18n.changeLanguage('es-MX')
  })
}
