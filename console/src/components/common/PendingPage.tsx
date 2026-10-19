import { Construction } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PageHeader } from './page'

/** Stand-in for a section whose real content lands in a later increment. */
export function PendingPage({ title, description }: { title: string; description: string }) {
  const { t } = useTranslation()
  return (
    <>
      <PageHeader title={title} description={description} />
      <Card className="animate-fade-in-up">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Construction className="size-4 text-muted-foreground" aria-hidden />
            {t('pending.badge')}
          </CardTitle>
          <CardDescription>{t('pending.body')}</CardDescription>
        </CardHeader>
      </Card>
    </>
  )
}
