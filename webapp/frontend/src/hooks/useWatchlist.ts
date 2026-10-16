import { useEffect, useState } from 'react'
import { fetchWatchlist, type WatchlistItem } from '@/lib/api'

export function useWatchlist() {
  const [items, setItems] = useState<WatchlistItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchWatchlist()
      .then((data) => {
        if (!cancelled) setItems(data)
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
  }, [])

  return { items, loading, error }
}
