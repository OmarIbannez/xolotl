/** Today as a Mexico City day (`YYYY-MM-DD`): a backfill range cannot end after it. */
export function mexicoCityToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Mexico_City' }).format(now)
}
