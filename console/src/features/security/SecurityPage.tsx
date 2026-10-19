import { useTranslation } from 'react-i18next'
import { PageHeader } from '@/components/common/page'
import { PasswordCard } from './PasswordCard'
import { TotpCard } from './TotpCard'

/** Account security for whoever is signed in, whatever their roles: TOTP and the password. */
export function SecurityPage() {
  const { t } = useTranslation()
  return (
    <>
      <PageHeader title={t('security.title')} description={t('security.description')} />
      <div className="max-w-3xl space-y-4">
        <TotpCard />
        <PasswordCard />
      </div>
    </>
  )
}
