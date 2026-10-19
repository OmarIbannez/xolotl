/**
 * Put `text` on the clipboard. Resolves false when the browser refuses (permission
 * denied, no user gesture); never rejects.
 *
 * The async Clipboard API only exists on secure origins (HTTPS or localhost). On a console
 * served over plain HTTP, such as an internal staging host, fall back to the old
 * select-and-copy route so the buttons keep working.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return copyWithSelection(text)
  }
}

function copyWithSelection(text: string): boolean {
  const field = document.createElement('textarea')
  field.value = text
  field.setAttribute('readonly', '')
  field.setAttribute('aria-hidden', 'true')
  field.style.position = 'fixed'
  field.style.top = '0'
  field.style.opacity = '0'
  document.body.append(field)
  try {
    field.select()
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    field.remove()
  }
}
