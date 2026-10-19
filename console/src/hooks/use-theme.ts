import { useCallback, useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark'
const KEY = 'xolotl.theme'
const listeners = new Set<() => void>()

function stored(): Theme | null {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null // storage can be blocked; fall back to the system preference
  }
}

function current(): Theme {
  return stored() ?? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
}

export function applyTheme(theme: Theme = current()) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export function useTheme() {
  const theme = useSyncExternalStore((cb) => {
    listeners.add(cb)
    return () => listeners.delete(cb)
  }, current)
  const toggle = useCallback(() => {
    const next: Theme = current() === 'dark' ? 'light' : 'dark'
    try {
      localStorage.setItem(KEY, next)
    } catch {
      // storage can be blocked; the choice then lasts until the next reload
    }
    applyTheme(next)
    listeners.forEach((l) => l())
  }, [])
  return { theme, toggle }
}
