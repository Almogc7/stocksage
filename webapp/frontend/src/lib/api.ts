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

export interface SignalOutcome {
  pending: boolean
  close_t1: number | null
  close_t3: number | null
  close_t5: number | null
  close_t10: number | null
  max_adverse_excursion: number | null
  first_barrier_hit: 'stop_loss' | 'take_profit' | 'none' | null
  r_multiple: number | null
}

export interface AlertSignal {
  id: number
  triggered_at: string
  alert_type: string
  message: string
  score: number | null
  verdict: string | null
  price_at_alert: number | null
  rsi: number | null
  atr: number | null
  stop_loss: number | null
  take_profit: number | null
  triggered_signals: string[]
  outcome: SignalOutcome
}

export interface CompositeSignalEntry {
  id: number
  signal_date: string
  regime: string
  total_score: number
  required_score: number
  trend_pts: number
  momentum_pts: number
  volume_pts: number
  rs_pts: number
  rs_ratio: number | null
  required_rs: number
  price: number
  atr: number | null
  stop_price: number | null
  stop_multiplier: number | null
  outcome: SignalOutcome
}

export interface SignalsResponse {
  symbol: string
  from: string | null
  alerts: AlertSignal[]
  composite_signals: CompositeSignalEntry[]
  backtest_run_id: string | null
  backtest_signals: CompositeSignalEntry[]
}

export function fetchSignals(
  symbol: string,
  opts: { from?: string; includeBacktest?: boolean } = {},
): Promise<SignalsResponse> {
  const qs = new URLSearchParams()
  if (opts.from) qs.set('from', opts.from)
  if (opts.includeBacktest) qs.set('include_backtest', 'true')
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return getJson(`/api/signals/${encodeURIComponent(symbol)}${suffix}`)
}
