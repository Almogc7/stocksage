import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { fetchSparklines, type SparklineSeries } from '@/lib/api'

const CHUNK_SIZE = 60
const DEBOUNCE_MS = 150

export type SparklineState = 'loading' | SparklineSeries | null

interface SparklineRegistry {
  results: Record<string, SparklineState>
  request: (symbol: string) => void
  range: string
}

const SparklineContext = createContext<SparklineRegistry | null>(null)

export function SparklineProvider({
  range,
  children,
}: {
  range: string
  children: ReactNode
}) {
  const [results, setResults] = useState<Record<string, SparklineState>>({})
  const pending = useRef<Set<string>>(new Set())
  const inFlight = useRef<Set<string>>(new Set())
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const flush = useCallback(() => {
    const symbols = Array.from(pending.current)
    pending.current.clear()
    if (symbols.length === 0) return

    for (let i = 0; i < symbols.length; i += CHUNK_SIZE) {
      const chunk = symbols.slice(i, i + CHUNK_SIZE)
      chunk.forEach((s) => inFlight.current.add(s))
      fetchSparklines(chunk, range)
        .then((res) => {
          setResults((prev) => ({ ...prev, ...res.data }))
        })
        .catch(() => {
          setResults((prev) => {
            const next = { ...prev }
            chunk.forEach((s) => {
              next[s] = null
            })
            return next
          })
        })
        .finally(() => {
          chunk.forEach((s) => inFlight.current.delete(s))
        })
    }
  }, [range])

  const request = useCallback(
    (symbol: string) => {
      if (
        symbol in results ||
        pending.current.has(symbol) ||
        inFlight.current.has(symbol)
      ) {
        return
      }
      setResults((prev) => ({ ...prev, [symbol]: 'loading' }))
      pending.current.add(symbol)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(flush, DEBOUNCE_MS)
    },
    [results, flush],
  )

  const value = useMemo(
    () => ({ results, request, range }),
    [results, request, range],
  )

  return (
    <SparklineContext.Provider value={value}>
      {children}
    </SparklineContext.Provider>
  )
}

export function useSparklineRegistry() {
  const ctx = useContext(SparklineContext)
  if (!ctx) {
    throw new Error('useSparklineRegistry must be used within SparklineProvider')
  }
  return ctx
}
