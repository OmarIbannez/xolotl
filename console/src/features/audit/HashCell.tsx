import { useTranslation } from 'react-i18next'
import { CopyButton } from '@/components/common/CopyButton'

/** How much of a 64-character hash the table shows; the copy button carries the rest. */
export const HASH_PREFIX_CHARS = 8

interface HashCellProps {
  /** The whole hash, in hex. */
  hash: string
  /** Accessible name of the copy button; say which event and which hash. */
  copyLabel: string
}

/** The first characters of a hash, in monospace, with a button that copies all of it. */
export function HashCell({ hash, copyLabel }: HashCellProps) {
  const { t } = useTranslation()
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-mono text-xs" title={hash}>
        {hash.slice(0, HASH_PREFIX_CHARS)}
      </span>
      <CopyButton
        value={hash}
        label={copyLabel}
        copiedMessage={t('audit.hash.copied')}
        failedMessage={t('audit.copyFailed')}
      />
    </span>
  )
}
