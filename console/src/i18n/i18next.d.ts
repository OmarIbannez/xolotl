import 'i18next'
import type esMX from './es-MX.json'

// Types every `t('...')` key against the default catalogue; the other catalogue is
// kept in step by the `satisfies` checks in ./index.ts.
declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation'
    resources: { translation: typeof esMX }
  }
}
