import { showInvisibles } from './safe-text'
import type { AuditDetail } from './types'

/** How many characters of an event's detail the table shows before offering the full view. */
export const DETAIL_PREVIEW_CHARS = 120

export interface DetailPreview {
  /** The detail as one line of JSON text, cut (and ended with an ellipsis) when it is long. */
  text: string
  truncated: boolean
}

/** Cut `text` after `max` characters, counting code points so no emoji is split in half. */
function truncate(text: string, max: number): DetailPreview {
  let end = 0
  let count = 0
  for (const char of text) {
    if (count === max) return { text: `${text.slice(0, end)}…`, truncated: true }
    end += char.length
    count += 1
  }
  return { text, truncated: false }
}

/** The table cell's view of a detail; null when the event has none. Always plain text. */
export function previewDetail(detail: AuditDetail): DetailPreview | null {
  if (detail === null) return null
  return truncate(showInvisibles(JSON.stringify(detail)), DETAIL_PREVIEW_CHARS)
}

/** The expanded view: the whole detail, indented. Always plain text. */
export function prettyDetail(detail: NonNullable<AuditDetail>): string {
  return showInvisibles(JSON.stringify(detail, null, 2), { keepLineFeeds: true })
}
