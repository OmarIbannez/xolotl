import { useMutation, type UseMutationOptions, type UseMutationResult } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { createIdempotentRunner, type Send } from './idempotency'

type IdempotentMutationOptions<TVars, TResult, TContext> = Omit<
  UseMutationOptions<TResult, Error, TVars, TContext>,
  'mutationFn'
> & {
  /**
   * Makes the call. Send `idempotencyKey` as the request's `Idempotency-Key` header: it is
   * the same for the retries of one submission and new for each new submission.
   */
  mutationFn: Send<TVars, TResult>
}

/**
 * `useMutation` for a POST the API makes idempotent (every POST under `/v1` but `/auth/*`).
 *
 * A key is chosen when the user presses submit and handed to `mutationFn`. If the request fails
 * without a definite answer (no response, 5xx, a request still running) and the user submits the
 * same values again, the same key goes out again, so the API replays the first answer instead of
 * creating the thing twice. Edited values, a success or a refusal make the next submission new.
 * A repeat while a request is in flight (a double click) joins it instead of sending another.
 * The rules live in `createIdempotentRunner`.
 *
 * The state belongs to the component that calls the hook: it ends with it, and `reset()` ends it
 * early (call it when the user abandons the submission).
 */
export function useIdempotentMutation<TVars, TResult, TContext = unknown>(
  options: IdempotentMutationOptions<TVars, TResult, TContext>,
): UseMutationResult<TResult, Error, TVars, TContext> {
  const { mutationFn, ...rest } = options
  const [runner] = useState(() => createIdempotentRunner<TVars, TResult>())
  const mutation = useMutation<TResult, Error, TVars, TContext>({
    ...rest,
    mutationFn: (vars) => runner.run(vars, mutationFn),
  })
  const { reset: resetMutation } = mutation
  const reset = useCallback(() => {
    runner.reset()
    resetMutation()
  }, [runner, resetMutation])
  return { ...mutation, reset }
}
