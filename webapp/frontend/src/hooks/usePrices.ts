import { useEffect, useState } from 'react'
import { fetchPrices, type PricesResponse } from '@/lib/api'

/** Always fetches 2y of daily history so SMA150/SMA200 have enough lead-in
 * to be visible at every visible-range selection, including 1mo. */
export function usePrices(symbol: string | null) {
  const [data, setData] = useState<PricesResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
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

  return { data, loading, error }
}
