import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface ConfirmDialogProps {
  title: string
  description: string
  confirmLabel: string
  /** The confirm button's label while the action runs. */
  pendingLabel: string
  pending: boolean
  /** How the last attempt failed, shown above the buttons; the dialog stays open to retry. */
  alert?: ReactNode
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Asks before a destructive action (revoke a key, disable an account). Open for as long as it
 * is rendered. While the action runs it cannot be dismissed, so its outcome is never lost.
 */
export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  pendingLabel,
  pending,
  alert,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation()
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onCancel()
      }}
    >
      <DialogContent showCloseButton={!pending}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {alert}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel} disabled={pending}>
            {t('common.cancel')}
          </Button>
          <Button type="button" variant="destructive" onClick={onConfirm} disabled={pending}>
            {pending ? pendingLabel : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
