import type { SparklineState } from '@/hooks/useSparklines'

/** % change from the first to the last close in a sparkline series, or null
 * when there isn't enough data yet (loading/missing/too few points). */
export function sparklinePct(state: SparklineState | undefined): number | null {
  if (!state || state === 'loading') return null
  const closes = state.closes
  if (closes.length < 2) return null
  const first = closes[0].close
  const last = closes.at(-1)!.close
  return ((last - first) / first) * 100
}

export function sparklineCloses(state: SparklineState | undefined): number[] {
  if (!state || state === 'loading') return []
  return state.closes.map((c) => c.close)
}
