import { Check, Copy } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { copyText } from '@/lib/clipboard'

interface CopyButtonProps {
  /** What lands on the clipboard. */
  value: string
  /** The button's accessible name, specific enough to tell it from its neighbours. */
  label: string
  /** Toast shown once the text is on the clipboard. */
  copiedMessage: string
  /** Toast shown when the browser refuses. */
  failedMessage: string
  className?: string
}

/** How long the check mark stays before the button goes back to its copy icon. */
const CONFIRMATION_MS = 1500

/** An icon button that copies `value`, and says so with a toast and a check mark. */
export function CopyButton({ value, label, copiedMessage, failedMessage, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  async function copy() {
    if (!(await copyText(value))) {
      toast.error(failedMessage)
      return
    }
    toast.success(copiedMessage)
    setCopied(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setCopied(false), CONFIRMATION_MS)
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      className={className}
      aria-label={label}
      title={label}
      onClick={() => void copy()}
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
    </Button>
  )
}
