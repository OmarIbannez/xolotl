import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { passwordStrength, type PasswordStrength } from './password-strength'

const LEVEL: Record<PasswordStrength, number> = { empty: 0, weak: 1, fair: 2, strong: 3 }
const FILL: Record<PasswordStrength, string> = {
  empty: 'bg-border',
  weak: 'bg-critical',
  fair: 'bg-warning',
  strong: 'bg-good',
}

/** Three segments plus a sentence: the color is never the only signal. */
export function PasswordStrengthHint({ password }: { password: string }) {
  const { t } = useTranslation()
  const strength = passwordStrength(password)
  return (
    <div aria-live="polite" className="space-y-1">
      <div className="flex gap-1" aria-hidden>
        {[1, 2, 3].map((segment) => (
          <span
            key={segment}
            className={cn('h-1 flex-1 rounded-full bg-border', LEVEL[strength] >= segment && FILL[strength])}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        <span className="sr-only">{t('auth.setupPassword.strength.label')}: </span>
        {t(`auth.setupPassword.strength.${strength}`)}
      </p>
    </div>
  )
}
