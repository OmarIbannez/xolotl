import { newIdempotencyKey } from './client'
import { isApiError } from './errors'

/**
 * Whether a failed request may nevertheless have taken effect on the server. When it may
 * have, asking again must carry the same `Idempotency-Key`, so the API replays the first
 * answer instead of doing the work twice (two API keys, two staff accounts).
 *
 * Unknown: no answer at all (network failure, status 0), a server or gateway fault (5xx), a
 * request with that key still running (409 `IDEMPOTENCY_IN_PROGRESS`), a success we could not
 * read, and anything that is not an API answer. Known: any other 4xx. The API refused the
 * request and released the key, so nothing happened and the next try is a fresh submission.
 */
export function outcomeIsUnknown(error: unknown): boolean {
  if (!isApiError(error)) return true
  return error.status < 400 || error.status >= 500 || error.code === 'IDEMPOTENCY_IN_PROGRESS'
}

export type Send<TVars, TResult> = (vars: TVars, idempotencyKey: string) => Promise<TResult>

export interface IdempotentRunner<TVars, TResult> {
  /**
   * Send one submission. Each submission carries one key. A repeat of a submission whose
   * request is still in flight (a double click) joins that request instead of sending
   * another. After a failure with an unknown outcome, the same values carry the same key
   * again; after a success, a refusal, or with other values, the key is new.
   */
  run(vars: TVars, send: Send<TVars, TResult>): Promise<TResult>
  /** Forget the key kept after an unknown failure (the user walked away from the submission). */
  reset(): void
}

/**
 * The key bookkeeping behind `useIdempotentMutation`, free of React so it can be tested alone.
 *
 * The API ties a key to the exact request (method, path and body), so a submission is
 * identified by its values: a retry is the same values, and edited values are a new
 * submission that needs a new key.
 */
export function createIdempotentRunner<TVars, TResult>(
  generateKey: () => string = newIdempotencyKey,
): IdempotentRunner<TVars, TResult> {
  /** The last submission whose outcome is not settled: its values and the key it was sent with. */
  let held: { fingerprint: string; key: string } | null = null
  /** The request on the wire, so a repeat of it can join. */
  let inFlight: { fingerprint: string; promise: Promise<TResult> } | null = null

  async function attempt(
    fingerprint: string,
    vars: TVars,
    send: Send<TVars, TResult>,
  ): Promise<TResult> {
    const key = held?.fingerprint === fingerprint ? held.key : generateKey()
    held = { fingerprint, key }
    try {
      const result = await send(vars, key)
      if (held?.key === key) held = null // done: the next submission is a new one
      return result
    } catch (error) {
      if (!outcomeIsUnknown(error) && held?.key === key) held = null // refused: nothing happened
      throw error
    }
  }

  return {
    run(vars, send) {
      const fingerprint = JSON.stringify(vars)
      if (inFlight?.fingerprint === fingerprint) return inFlight.promise
      const entry = { fingerprint, promise: attempt(fingerprint, vars, send) }
      inFlight = entry
      const settled = () => {
        if (inFlight === entry) inFlight = null
      }
      entry.promise.then(settled, settled)
      return entry.promise
    },

    reset() {
      if (inFlight === null) held = null
    },
  }
}
