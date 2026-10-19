import { useMutation } from '@tanstack/react-query'
import { api, expectNoContent, unwrap } from '@/api/client'

/**
 * Start TOTP enrolment: the API makes a secret and answers with it and the `otpauth://`
 * address that an authenticator app reads from a QR code. Asking again before confirming
 * replaces the secret.
 *
 * The secret is shown once and never fetched again, so the answer is not kept: nothing
 * holds on to it once the page lets go of this mutation (`gcTime: 0`), and the page resets
 * it as soon as the enrolment is confirmed or cancelled.
 */
export function useEnrollTotp() {
  return useMutation({
    mutationFn: () => unwrap(api.POST('/auth/totp/enroll')),
    gcTime: 0,
  })
}

/**
 * Finish enrolment with a code from the authenticator app (422 when the code is wrong).
 * The code travels as the mutation's variables, so like the secret it is not kept once the
 * panel lets go of the mutation (`gcTime: 0`): a spent one-time code has no business in memory.
 */
export function useConfirmTotp() {
  return useMutation({
    mutationFn: (code: string) => expectNoContent(api.POST('/auth/totp/confirm', { body: { code } })),
    gcTime: 0,
  })
}
