import { Ban } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { buttonVariants } from '@/components/ui/button'
import { MessagePage } from './MessagePage'

/** Shown in place of a route the signed-in user's roles do not allow. */
export function Forbidden() {
  const { t } = useTranslation()
  return (
    <MessagePage
      icon={Ban}
      title={t('forbidden.title')}
      actions={
        <Link to="/" className={buttonVariants({ size: 'lg' })}>
          {t('common.goHome')}
        </Link>
      }
    >
      {t('forbidden.body')}
    </MessagePage>
  )
}
