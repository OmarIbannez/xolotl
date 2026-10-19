/**
 * A calendar day from the API (`YYYY-MM-DD`) as a local `Date`, for `useFormat().date`. Read as an
 * instant, `'2037-03-01'` is UTC midnight and shows as the day before west of Greenwich.
 */
export function calendarDay(value: string): Date {
  const [year = 0, month = 1, day = 1] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}
