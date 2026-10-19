import { formatShortDate, formatTime } from '@/lib/format'

/** The `<time>` element a `DateTimeCell` rendered inside `container` for this instant. */
export function timeCell(container: HTMLElement, iso: string): HTMLElement {
  const cell = container.querySelector<HTMLElement>(`time[datetime="${iso}"]`)
  if (!cell) throw new Error(`no <time datetime="${iso}"> in the container`)
  return cell
}

/** What a `DateTimeCell` shows (es-MX) for an instant: the date, then the time. */
export function shownAt(iso: string): string {
  return `${formatShortDate(iso, 'es-MX')}${formatTime(iso, 'es-MX')}`
}
