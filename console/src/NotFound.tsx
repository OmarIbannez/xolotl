import { SearchX } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { MessagePage } from '@/components/common/MessagePage'
import { buttonVariants } from '@/components/ui/button'

export function NotFound() {
  const { t } = useTranslation()
  return (
    <MessagePage
      icon={SearchX}
      title={t('notFound.title')}
      actions={
        <Link to="/" className={buttonVariants({ size: 'lg' })}>
          {t('common.goHome')}
        </Link>
      }
    >
      {t('notFound.body')}
    </MessagePage>
  )
}
