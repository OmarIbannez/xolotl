import { cn } from '@/lib/utils'

/** How many trailing characters stay visible when the identifier is cut. */
const TAIL_CHARS = 8

interface TruncatedIdProps {
  /** The whole identifier, already safe to print. */
  value: string
  className?: string
}

/**
 * An identifier cut in the middle, so the part that tells one apart from the next stays in
 * view. The front of a UUIDv7 is its timestamp and reads the same for every id made around
 * the same time; the end is random. The whole value remains in the text (it can be selected
 * and searched) and in the hover title, and the cut is done by the browser's own ellipsis
 * so it follows the width of the column.
 */
export function TruncatedId({ value, className }: TruncatedIdProps) {
  const chars = Array.from(value) // by code point, so no character is split in half
  const head = chars.slice(0, Math.max(0, chars.length - TAIL_CHARS)).join('')
  const tail = chars.slice(-TAIL_CHARS).join('')
  return (
    <span title={value} className={cn('flex max-w-28 font-mono text-xs', className)}>
      <span className="min-w-0 truncate">{head}</span>
      <span className="shrink-0">{tail}</span>
    </span>
  )
}
