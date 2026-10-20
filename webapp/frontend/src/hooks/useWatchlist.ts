import { useCallback, useEffect, useState } from 'react'
import { fetchWatchlist, type WatchlistItem } from '@/lib/api'

export function useWatchlist() {
  const [items, setItems] = useState<WatchlistItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null)
  const [generation, setGeneration] = useState(0)

  const refetch = useCallback(() => setGeneration((g) => g + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchWatchlist()
      .then((data) => {
        if (!cancelled) {
          setItems(data)
          setFetchedAt(new Date())
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [generation])

  return { items, loading, error, fetchedAt, refetch }
}
