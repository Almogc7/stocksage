export type WlState =
  | 'ACTIVE'
  | 'MONITOR'
  | 'ETF_INDEX_CONTEXT'
  | 'TEMPORARILY_INELIGIBLE'
  | 'USER_REMOVED'

export interface WatchlistItem {
  symbol: string
  category: string
  wl_state: WlState
  pinned: boolean
}

export type SmaPosition = 'above' | 'below' | null

export interface SparklineSeries {
  symbol: string
  range: string
  closes: { time: string; close: number }[]
  sma150_pos: SmaPosition
  sma200_pos: SmaPosition
}

export interface SparklinesResponse {
  range: string
  data: Record<string, SparklineSeries | null>
}

export type PriceRange = '1mo' | '3mo' | '6mo' | '1y' | '2y' | '5y'

export interface Candle {
  time: string
  open: number
  high: number
  low: number
  close: number
}

export interface VolumeBar {
  time: string
  value: number
}

export interface PricesResponse {
  symbol: string
  range: PriceRange
  interval: string
  candles: Candle[]
  volume: VolumeBar[]
  stale?: boolean
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    throw new Error(`${url} -> ${res.status}`)
  }
  return res.json() as Promise<T>
}

export function fetchWatchlist(): Promise<WatchlistItem[]> {
  return getJson('/api/watchlist')
}

export function fetchSparklines(
  symbols: string[],
  range: string,
): Promise<SparklinesResponse> {
  const qs = new URLSearchParams({ symbols: symbols.join(','), range })
  return getJson(`/api/sparklines?${qs.toString()}`)
}

export function fetchPrices(
  symbol: string,
  range: PriceRange,
): Promise<PricesResponse> {
  const qs = new URLSearchParams({ range })
  return getJson(`/api/prices/${encodeURIComponent(symbol)}?${qs.toString()}`)
}
