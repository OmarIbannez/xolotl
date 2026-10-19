/**
 * The staff session: an access token and the two signals the API client raises
 * about it. It is a tiny external store (read it with `useSyncExternalStore`) so the
 * API client can end the session without knowing anything about React or the router.
 *
 * The token lives in memory and is mirrored to `sessionStorage`, so a reload keeps
 * the session but closing the tab ends it. It is never written to `localStorage`.
 */

const STORAGE_KEY = 'xolotl.token'

export interface SessionSnapshot {
  /** The bearer token, or null when nobody is signed in. */
  readonly token: string | null
  /** The API answered 403 PASSWORD_CHANGE_REQUIRED for this token. */
  readonly passwordChangeRequired: boolean
  /**
   * How the last session in this page's lifetime ended: the user signed out, or the
   * API rejected the token (401). Null before any session has ended.
   */
  readonly endedBy: 'signedOut' | 'expired' | null
}

function readStoredToken(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY)
  } catch {
    return null // storage can be blocked; the session then lives in memory only
  }
}

function writeStoredToken(token: string | null): void {
  try {
    if (token === null) window.sessionStorage.removeItem(STORAGE_KEY)
    else window.sessionStorage.setItem(STORAGE_KEY, token)
  } catch {
    // storage can be blocked; the session then lives in memory only
  }
}

let snapshot: SessionSnapshot = {
  token: readStoredToken(),
  passwordChangeRequired: false,
  endedBy: null,
}
const listeners = new Set<() => void>()

function publish(next: SessionSnapshot): void {
  snapshot = next
  for (const listener of listeners) listener()
}

export const session = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },

  getSnapshot(): SessionSnapshot {
    return snapshot
  },

  getToken(): string | null {
    return snapshot.token
  },

  /** A successful login: adopt the new token. */
  start(token: string): void {
    writeStoredToken(token)
    publish({ token, passwordChangeRequired: false, endedBy: null })
  },

  /** The user signed out. */
  end(): void {
    writeStoredToken(null)
    publish({ token: null, passwordChangeRequired: false, endedBy: 'signedOut' })
  },

  /**
   * The API rejected `rejectedToken` with 401. Clears the session only when that is
   * still the current token, so a late 401 for an old request cannot sign out a
   * newer login.
   */
  expire(rejectedToken: string | undefined): void {
    if (rejectedToken === undefined || rejectedToken !== snapshot.token) return
    writeStoredToken(null)
    publish({ token: null, passwordChangeRequired: false, endedBy: 'expired' })
  },

  /**
   * The API answered 403 PASSWORD_CHANGE_REQUIRED to a request that carried `rejectedToken`:
   * route to /setup-password. Raises the gate only when that is still the current token, so a
   * late answer to a request sent before a password change (which hands out a new token)
   * cannot gate the session that followed it.
   */
  requirePasswordChange(rejectedToken: string | undefined): void {
    if (rejectedToken === undefined || rejectedToken !== snapshot.token) return
    if (snapshot.passwordChangeRequired) return
    publish({ ...snapshot, passwordChangeRequired: true })
  },

  /** The password was changed: the gate lifts. */
  clearPasswordChange(): void {
    if (!snapshot.passwordChangeRequired) return
    publish({ ...snapshot, passwordChangeRequired: false })
  },
}
