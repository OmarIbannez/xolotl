/**
 * What the audit list is showing: which action it is filtered to and which page of
 * results. The list is paged with opaque cursors that only go forward, so going back means
 * remembering the cursor that opened each earlier page.
 */
export interface AuditView {
  /** The exact action to show; '' shows every event. */
  readonly action: string
  /** The cursor that opened each page visited, first page first (undefined: the newest). */
  readonly cursors: readonly (string | undefined)[]
}

export type AuditViewEvent =
  | { type: 'filter'; action: string }
  | { type: 'next'; cursor: string }
  | { type: 'back' }

export const FIRST_PAGE: AuditView = { action: '', cursors: [undefined] }

export function auditViewReducer(view: AuditView, event: AuditViewEvent): AuditView {
  switch (event.type) {
    case 'filter':
      // A new filter is a new list: start again from the newest event. The same filter
      // again (the debounce firing after Enter, say) changes nothing.
      return event.action === view.action ? view : { action: event.action, cursors: [undefined] }
    case 'next':
      // The same cursor twice is a double click on "next".
      return view.cursors.at(-1) === event.cursor
        ? view
        : { ...view, cursors: [...view.cursors, event.cursor] }
    case 'back':
      return view.cursors.length <= 1 ? view : { ...view, cursors: view.cursors.slice(0, -1) }
  }
}

/** The cursor of the page on screen; undefined for the first page. */
export function currentCursor(view: AuditView): string | undefined {
  return view.cursors.at(-1)
}

/** 1 for the first page. */
export function pageNumber(view: AuditView): number {
  return view.cursors.length
}
