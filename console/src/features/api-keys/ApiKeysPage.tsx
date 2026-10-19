import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { useApiKeys, useRevokeApiKey } from '@/api/queries'
import type { ApiKeyCreatedResponse, ApiKeyResponse } from '@/api/types'
import { ApiKeyStatusBadge, EnvBadge, ScopeBadges } from '@/components/common/badges'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { OneTimeSecretDialog } from '@/components/common/OneTimeSecretDialog'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { cn } from '@/lib/utils'
import { CreateApiKeyDialog } from './CreateApiKeyDialog'

/** The new key's name and value, on screen only until the user has saved it. */
interface ShownSecret {
  name: string
  key: string
}

/** API keys of the tenant (ADMIN): list, create (the key is shown once) and revoke. */
export function ApiKeysPage() {
  const { t } = useTranslation()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const keys = useApiKeys(cursor)
  const revoke = useRevokeApiKey()
  const [creating, setCreating] = useState(false)
  const [secret, setSecret] = useState<ShownSecret | null>(null)
  const [revoking, setRevoking] = useState<ApiKeyResponse | null>(null)

  function handleCreated(created: ApiKeyCreatedResponse) {
    setCreating(false)
    setCursor(undefined) // the new key is the newest, so it is on the first page
    setSecret({ name: created.name, key: created.key })
  }

  function askToRevoke(key: ApiKeyResponse) {
    revoke.reset()
    setRevoking(key)
  }

  const columns: Column<ApiKeyResponse>[] = [
    {
      id: 'name',
      header: t('apiKeys.table.name'),
      cell: (key) => <span className="font-medium">{key.name}</span>,
      // A name is up to 120 characters of the admin's choosing: wrap it rather than stretch the table.
      className: 'min-w-36 max-w-64 whitespace-normal break-words',
    },
    {
      id: 'prefix',
      header: t('apiKeys.table.prefix'),
      cell: (key) => <code className="font-mono text-xs">{key.key_prefix}</code>,
    },
    {
      id: 'scopes',
      header: t('apiKeys.table.scopes'),
      cell: (key) => <ScopeBadges scopes={key.scopes} />,
      className: 'min-w-44 whitespace-normal',
    },
    { id: 'env', header: t('apiKeys.table.env'), cell: (key) => <EnvBadge env={key.env} /> },
    {
      id: 'status',
      header: t('apiKeys.table.status'),
      cell: (key) => <ApiKeyStatusBadge status={key.status} />,
    },
    {
      id: 'createdAt',
      header: t('apiKeys.table.createdAt'),
      cell: (key) => <DateTimeCell value={key.created_at} />,
    },
    {
      id: 'lastUsedAt',
      header: t('apiKeys.table.lastUsedAt'),
      cell: (key) =>
        key.last_used_at ? <DateTimeCell value={key.last_used_at} /> : t('common.never'),
    },
    {
      id: 'actions',
      header: t('apiKeys.table.actions'),
      className: 'text-right',
      cell: (key) =>
        key.status === 'ACTIVE' ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={t('apiKeys.revoke.actionFor', { name: key.name })}
            onClick={() => askToRevoke(key)}
          >
            {t('apiKeys.revoke.action')}
          </Button>
        ) : null,
    },
  ]

  const page = keys.data
  // Past the first page the pager stays even if the page failed to load, so the user can go back.
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  return (
    <>
      <PageHeader
        title={t('apiKeys.title')}
        description={t('apiKeys.description')}
        actions={
          <Button type="button" onClick={() => setCreating(true)}>
            <Plus aria-hidden />
            {t('apiKeys.new')}
          </Button>
        }
      />

      <Card className="animate-fade-in-up">
        <CardContent>
          {keys.isError ? (
            <ErrorBlock error={keys.error} onRetry={() => void keys.refetch()} />
          ) : (
            <div className={cn(keys.isPlaceholderData && 'opacity-60 transition-opacity')}>
              <DataTable
                label={t('apiKeys.table.label')}
                columns={columns}
                rows={page?.items}
                getRowId={(key) => key.id}
                loading={keys.isPending}
                empty={t('apiKeys.empty')}
                rowClassName={(key) => (key.status === 'REVOKED' ? 'bg-muted/30 text-muted-foreground' : undefined)}
              />
            </div>
          )}
        </CardContent>
        {showPager && (
          <CardFooter>
            <div className="w-full">
              <CursorPager
                cursor={cursor}
                nextCursor={page?.next_cursor}
                onChange={setCursor}
                busy={keys.isPlaceholderData}
              />
            </div>
          </CardFooter>
        )}
      </Card>

      {creating && <CreateApiKeyDialog onClose={() => setCreating(false)} onCreated={handleCreated} />}

      {secret && (
        <OneTimeSecretDialog
          title={t('apiKeys.secret.title', { name: secret.name })}
          secret={secret.key}
          hint={t('apiKeys.secret.hint')}
          onClose={() => setSecret(null)}
        />
      )}

      {revoking && (
        <ConfirmDialog
          title={t('apiKeys.revoke.title', { name: revoking.name })}
          description={t('apiKeys.revoke.body')}
          confirmLabel={t('apiKeys.revoke.confirm')}
          pendingLabel={t('apiKeys.revoke.confirming')}
          pending={revoke.isPending}
          alert={revoke.isError ? <FormAlert error={revoke.error} /> : undefined}
          onConfirm={() =>
            revoke.mutate(revoking.id, {
              onSuccess: () => {
                toast.success(t('apiKeys.revoke.done', { name: revoking.name }))
                setRevoking(null)
              },
            })
          }
          onCancel={() => {
            revoke.reset()
            setRevoking(null)
          }}
        />
      )}
    </>
  )
}
