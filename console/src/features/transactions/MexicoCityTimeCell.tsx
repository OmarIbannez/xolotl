import { useFormat } from '@/i18n/use-locale'
import { mexicoCityDate, mexicoCityDateTime, mexicoCityTime } from './time'

/** A timestamp for a table cell, as `DateTimeCell` lays it out, but in Mexico City time. */
export function MexicoCityTimeCell({ value }: { value: string }) {
  const { locale } = useFormat()
  return (
    <time dateTime={value} title={mexicoCityDateTime(value, locale)} className="block leading-tight">
      <span className="block">{mexicoCityDate(value, locale)}</span>
      <span className="block text-xs text-muted-foreground">{mexicoCityTime(value, locale)}</span>
    </time>
  )
}
