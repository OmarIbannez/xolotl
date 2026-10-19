import { useFormat } from '@/i18n/use-locale'

/**
 * A timestamp for a table cell: the date over the time, so it takes half the width of
 * "Oct 18, 2026, 5:43 PM" on one line. The `<time>` element carries the exact instant, and
 * the tooltip the whole thing in the viewer's locale and time zone.
 */
export function DateTimeCell({ value }: { value: string }) {
  const format = useFormat()
  return (
    <time dateTime={value} title={format.dateTime(value)} className="block leading-tight">
      <span className="block">{format.shortDate(value)}</span>
      <span className="block text-xs text-muted-foreground">{format.time(value)}</span>
    </time>
  )
}
