import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { hasProblemCode } from '@/api/errors'
import { useDisableStaff, useStaff } from '@/api/queries'
import type { StaffCreatedResponse, StaffResponse } from '@/api/types'
import { useUser } from '@/auth/use-auth'
import { RoleBadges, StaffStatusBadge } from '@/components/common/badges'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { CursorPager } from '@/components/common/CursorPager'
import { DataTable, type Column } from '@/components/common/DataTable'
import { DateTimeCell } from '@/components/common/DateTimeCell'
import { OneTimeSecretDialog } from '@/components/common/OneTimeSecretDialog'
import { PageHeader } from '@/components/common/page'
import { ErrorBlock } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { FormAlert } from '@/features/auth/FormAlert'
import { cn } from '@/lib/utils'
import { CreateStaffDialog } from './CreateStaffDialog'

/** The new account's name, email and first password, on screen only until the user has saved it. */
interface ShownSecret {
  name: string
  email: string
  password: string
}

/** Staff of the tenant (ADMIN): list, create (the initial password is shown once) and disable. */
export function StaffPage() {
  const { t } = useTranslation()
  const me = useUser()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const staff = useStaff(cursor)
  const disable = useDisableStaff()
  const [creating, setCreating] = useState(false)
  const [secret, setSecret] = useState<ShownSecret | null>(null)
  const [disabling, setDisabling] = useState<StaffResponse | null>(null)

  function handleCreated(created: StaffCreatedResponse) {
    setCreating(false)
    setCursor(undefined) // the new account is the newest, so it is on the first page
    setSecret({ name: created.display_name, email: created.email, password: created.initial_password })
  }

  function askToDisable(user: StaffResponse) {
    disable.reset()
    setDisabling(user)
  }

  const columns: Column<StaffResponse>[] = [
    {
      id: 'email',
      header: t('staff.table.email'),
      cell: (user) => (
        <span className="flex items-center gap-2">
          <span className="font-medium">{user.email}</span>
          {user.id === me.id && <Badge variant="muted">{t('common.you')}</Badge>}
        </span>
      ),
      // Wrap a long address rather than stretch the table.
      className: 'min-w-48 max-w-64 whitespace-normal break-words',
    },
    {
      id: 'displayName',
      header: t('staff.table.displayName'),
      cell: (user) => user.display_name,
      className: 'min-w-32 max-w-48 whitespace-normal break-words',
    },
    {
      id: 'roles',
      header: t('staff.table.roles'),
      cell: (user) => <RoleBadges roles={user.roles} />,
      className: 'min-w-44 whitespace-normal',
    },
    {
      id: 'status',
      header: t('staff.table.status'),
      cell: (user) => <StaffStatusBadge status={user.status} />,
    },
    {
      id: 'mustChangePassword',
      header: t('staff.table.mustChangePassword'),
      cell: (user) =>
        user.must_change_password ? (
          <Badge variant="warning">{t('common.yes')}</Badge>
        ) : (
          <span className="text-muted-foreground">{t('common.no')}</span>
        ),
      // The header is a sentence: let it take two lines.
      className: 'max-w-36 whitespace-normal',
    },
    {
      id: 'createdAt',
      header: t('staff.table.createdAt'),
      cell: (user) => <DateTimeCell value={user.created_at} />,
    },
    {
      id: 'actions',
      header: t('staff.table.actions'),
      className: 'text-right',
      // Nobody can disable their own account: the API refuses it, so the console does not offer it.
      cell: (user) =>
        user.status === 'ACTIVE' && user.id !== me.id ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={t('staff.disable.actionFor', { name: user.display_name })}
            onClick={() => askToDisable(user)}
          >
            {t('staff.disable.action')}
          </Button>
        ) : null,
    },
  ]

  const page = staff.data
  // Past the first page the pager stays even if the page failed to load, so the user can go back.
  const showPager = cursor !== undefined || (page !== undefined && page.items.length > 0)

  return (
    <>
      <PageHeader
        title={t('staff.title')}
        description={t('staff.description')}
        actions={
          <Button type="button" onClick={() => setCreating(true)}>
            <Plus aria-hidden />
            {t('staff.new')}
          </Button>
        }
      />

      <Card className="animate-fade-in-up">
        <CardContent>
          {staff.isError ? (
            <ErrorBlock error={staff.error} onRetry={() => void staff.refetch()} />
          ) : (
            <div className={cn(staff.isPlaceholderData && 'opacity-60 transition-opacity')}>
              <DataTable
                label={t('staff.table.label')}
                columns={columns}
                rows={page?.items}
                getRowId={(user) => user.id}
                loading={staff.isPending}
                empty={t('staff.empty')}
                rowClassName={(user) => (user.status === 'DISABLED' ? 'bg-muted/30 text-muted-foreground' : undefined)}
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
                busy={staff.isPlaceholderData}
              />
            </div>
          </CardFooter>
        )}
      </Card>

      {creating && <CreateStaffDialog onClose={() => setCreating(false)} onCreated={handleCreated} />}

      {secret && (
        <OneTimeSecretDialog
          title={t('staff.secret.title', { name: secret.name })}
          secret={secret.password}
          hint={t('staff.secret.hint', { email: secret.email })}
          onClose={() => setSecret(null)}
        />
      )}

      {disabling && (
        <ConfirmDialog
          title={t('staff.disable.title', { name: disabling.display_name })}
          description={t('staff.disable.body', { email: disabling.email })}
          confirmLabel={t('staff.disable.confirm')}
          pendingLabel={t('staff.disable.confirming')}
          pending={disable.isPending}
          alert={
            disable.isError ? (
              <FormAlert
                error={disable.error}
                // The only conflict a disable can raise is disabling yourself.
                whenStatus={
                  hasProblemCode(disable.error, 'CONFLICT')
                    ? { status: 409, message: t('staff.disable.conflict') }
                    : undefined
                }
              />
            ) : undefined
          }
          onConfirm={() =>
            disable.mutate(disabling.id, {
              onSuccess: () => {
                toast.success(t('staff.disable.done', { name: disabling.display_name }))
                setDisabling(null)
              },
            })
          }
          onCancel={() => {
            disable.reset()
            setDisabling(null)
          }}
        />
      )}
    </>
  )
}
