import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchSignals, type SignalsResponse } from '@/lib/api'
import { dedupeAsync } from '@/lib/dedupeAsync'

/** Fetches legacy-alert + composite-signal history for a symbol up front
 * (includeBacktest: false). Backtest rows are heavier and informational
 * only, so they're fetched lazily via loadBacktest() the first time the
 * user turns that source on, then cached for the symbol's lifetime. */
export function useSignals(symbol: string | null) {
  const [data, setData] = useState<SignalsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const backtestCache = useRef<Map<string, SignalsResponse>>(new Map())
  const backtestInFlight = useRef<Map<string, Promise<SignalsResponse>>>(new Map())

  useEffect(() => {
    backtestCache.current.clear()
    if (!symbol) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchSignals(symbol)
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

  const loadBacktest = useCallback(async (): Promise<SignalsResponse | null> => {
    if (!symbol) return null
    const cached = backtestCache.current.get(symbol)
    if (cached) {
      setData(cached)
      return cached
    }
    const res = await dedupeAsync(symbol, backtestInFlight.current, () =>
      fetchSignals(symbol, { includeBacktest: true }),
    )
    backtestCache.current.set(symbol, res)
    setData(res)
    return res
  }, [symbol])

  return { data, loading, error, loadBacktest }
}
