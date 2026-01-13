import { useState, useEffect } from 'react'

/**
 * Hook to ensure animations only run after hydration.
 * Prevents SSR/client mismatch for animated components.
 */
export function useHydrated() {
  const [hydrated, setHydrated] = useState(false)
  useEffect(() => {
    setHydrated(true)
  }, [])
  return hydrated
}
