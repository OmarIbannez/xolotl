import { afterEach, describe, expect, it, vi } from 'vitest'

const KEY = 'xolotl.token'

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial))
  return {
    data,
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      data.set(key, value)
    }),
    removeItem: vi.fn((key: string) => {
      data.delete(key)
    }),
  }
}

/** Load a fresh copy of the store, as a page load would, with the given browser storage. */
async function loadSession(storage: object, local: object = memoryStorage()) {
  vi.resetModules()
  vi.stubGlobal('window', { sessionStorage: storage })
  vi.stubGlobal('localStorage', local)
  return (await import('./session')).session
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('session store', () => {
  it('restores the token a reload left in sessionStorage', async () => {
    const session = await loadSession(memoryStorage({ [KEY]: 'stored-token' }))

    expect(session.getToken()).toBe('stored-token')
  })

  it('starts empty when nothing is stored', async () => {
    const session = await loadSession(memoryStorage())

    expect(session.getSnapshot()).toEqual({ token: null, passwordChangeRequired: false, endedBy: null })
  })

  it('keeps the token in memory and mirrors it to sessionStorage, never to localStorage', async () => {
    const storage = memoryStorage()
    const local = memoryStorage()
    const session = await loadSession(storage, local)

    session.start('tok-1')

    expect(session.getToken()).toBe('tok-1')
    expect(storage.data.get(KEY)).toBe('tok-1')
    expect(local.setItem).not.toHaveBeenCalled()
    expect(local.getItem).not.toHaveBeenCalled()
  })

  it('removes the stored token when the user signs out', async () => {
    const storage = memoryStorage({ [KEY]: 'tok-1' })
    const session = await loadSession(storage)

    session.end()

    expect(session.getToken()).toBeNull()
    expect(storage.data.has(KEY)).toBe(false)
    expect(session.getSnapshot().endedBy).toBe('signedOut')
  })

  it('expires the session when the rejected token is the current one', async () => {
    const storage = memoryStorage({ [KEY]: 'tok-1' })
    const session = await loadSession(storage)

    session.expire('tok-1')

    expect(session.getToken()).toBeNull()
    expect(storage.data.has(KEY)).toBe(false)
    expect(session.getSnapshot().endedBy).toBe('expired')
  })

  it('ignores a rejection of another token, or of a request that carried none', async () => {
    const session = await loadSession(memoryStorage({ [KEY]: 'tok-2' }))

    session.expire('tok-1')
    session.expire(undefined)

    expect(session.getToken()).toBe('tok-2')
    expect(session.getSnapshot().endedBy).toBeNull()
  })

  it('tracks the password-change gate and drops it with the session', async () => {
    const session = await loadSession(memoryStorage({ [KEY]: 'tok-1' }))

    session.requirePasswordChange('tok-1')
    expect(session.getSnapshot().passwordChangeRequired).toBe(true)

    session.clearPasswordChange()
    expect(session.getSnapshot().passwordChangeRequired).toBe(false)

    session.requirePasswordChange('tok-1')
    session.end()
    expect(session.getSnapshot().passwordChangeRequired).toBe(false)
  })

  it('does not raise the gate when nobody is signed in', async () => {
    const session = await loadSession(memoryStorage())

    session.requirePasswordChange('tok-1')

    expect(session.getSnapshot().passwordChangeRequired).toBe(false)
  })

  it('raises the gate only for the token the API refused, like expire', async () => {
    const session = await loadSession(memoryStorage({ [KEY]: 'tok-2' }))

    session.requirePasswordChange('tok-1') // a late answer to a request sent with an older token
    session.requirePasswordChange(undefined) // a request that carried no bearer

    expect(session.getSnapshot().passwordChangeRequired).toBe(false)

    session.requirePasswordChange('tok-2')

    expect(session.getSnapshot().passwordChangeRequired).toBe(true)
  })

  it('a new login resets the gate and the way the last session ended', async () => {
    const session = await loadSession(memoryStorage({ [KEY]: 'tok-1' }))
    session.requirePasswordChange('tok-1')
    session.expire('tok-1')

    session.start('tok-2')

    expect(session.getSnapshot()).toEqual({ token: 'tok-2', passwordChangeRequired: false, endedBy: null })
  })

  it('notifies subscribers on change, and hands out a stable snapshot between changes', async () => {
    const session = await loadSession(memoryStorage())
    const listener = vi.fn()
    const unsubscribe = session.subscribe(listener)
    const before = session.getSnapshot()

    expect(session.getSnapshot()).toBe(before) // useSyncExternalStore needs a cached snapshot

    session.start('tok-1')
    expect(listener).toHaveBeenCalledTimes(1)
    expect(session.getSnapshot()).not.toBe(before)

    unsubscribe()
    session.end()
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('keeps working in memory when sessionStorage is blocked', async () => {
    const blocked = {
      getItem: () => {
        throw new DOMException('denied', 'SecurityError')
      },
      setItem: () => {
        throw new DOMException('denied', 'SecurityError')
      },
      removeItem: () => {
        throw new DOMException('denied', 'SecurityError')
      },
    }
    const session = await loadSession(blocked)

    expect(session.getToken()).toBeNull()
    session.start('tok-1')
    expect(session.getToken()).toBe('tok-1')
    session.end()
    expect(session.getToken()).toBeNull()
  })
})
