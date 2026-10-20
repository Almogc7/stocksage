import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchPrices, type PriceRange, type PricesResponse } from '@/lib/api'

/** Fetches 2y of daily history up front so SMA150/SMA200 have enough
 * lead-in to be visible at every visible-range selection up to 2y. 5y is
 * fetched on demand only (and cached in memory for the component's
 * lifetime) since it's a much heavier payload most sessions never need. */
export function usePrices(symbol: string | null) {
  const [data, setData] = useState<PricesResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fiveYearCache = useRef<Map<string, PricesResponse>>(new Map())

  useEffect(() => {
    fiveYearCache.current.clear()
    if (!symbol) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchPrices(symbol, '2y')
      .then((res) => {
        if (!cancelled) setData(res)
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
  }, [symbol])

  const fetchFiveYear = useCallback(async (): Promise<PricesResponse | null> => {
    if (!symbol) return null
    const cached = fiveYearCache.current.get(symbol)
    if (cached) return cached
    const res = await fetchPrices(symbol, '5y')
    fiveYearCache.current.set(symbol, res)
    return res
  }, [symbol])

  return { data, loading, error, fetchFiveYear }
}

export type { PriceRange }
