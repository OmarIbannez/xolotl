import { Check, Copy } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'

interface OneTimeSecretDialogProps {
  title: string
  /** The value the API will never show again: a new API key, an initial password. */
  secret: string
  /** Says what the secret is for and that it cannot be shown again. */
  hint: string
  onClose: () => void
}

const COPIED_FOR_MS = 2000

/**
 * Shows a one-time secret exactly once. It is open for as long as it is rendered: the
 * caller renders it when it has a secret and stops when `onClose` is called.
 *
 * The secret exists only in the props: this component stores it nowhere else (no state,
 * no storage, no log), so once the caller drops it, it is gone. To make sure it was saved,
 * the dialog cannot be closed (button, Escape, a click outside) until the user ticks
 * "I saved it".
 */
export function OneTimeSecretDialog({ title, secret, hint, onClose }: OneTimeSecretDialogProps) {
  const { t } = useTranslation()
  const [acknowledged, setAcknowledged] = useState(false)
  const [copied, setCopied] = useState(false)
  const acknowledgeId = useId()
  const copiedTimer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(copiedTimer.current), [])

  async function copy() {
    try {
      await navigator.clipboard.writeText(secret)
    } catch {
      // No clipboard (an insecure page) or permission denied: the text is selectable, copy it by hand.
      toast.error(t('common.oneTime.copyFailed'))
      return
    }
    toast.success(t('common.oneTime.copiedToast'))
    setCopied(true)
    window.clearTimeout(copiedTimer.current)
    copiedTimer.current = window.setTimeout(() => setCopied(false), COPIED_FOR_MS)
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && acknowledged) onClose()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{hint}</DialogDescription>
        </DialogHeader>

        <div className="flex items-start gap-2">
          <code className="min-w-0 flex-1 rounded-lg border bg-surface-2 px-3 py-2 font-mono text-sm break-all select-all">
            {secret}
          </code>
          <Button type="button" variant="outline" onClick={() => void copy()}>
            {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
            {copied ? t('common.oneTime.copied') : t('common.oneTime.copy')}
          </Button>
        </div>

        <div className="flex items-center gap-2">
          <Checkbox
            id={acknowledgeId}
            checked={acknowledged}
            onCheckedChange={(checked) => setAcknowledged(checked === true)}
          />
          <Label htmlFor={acknowledgeId}>{t('common.oneTime.acknowledge')}</Label>
        </div>

        <DialogFooter className="items-center sm:justify-between">
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {acknowledged ? '' : t('common.oneTime.acknowledgeRequired')}
          </p>
          <Button type="button" onClick={onClose} disabled={!acknowledged}>
            {t('common.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
