import type { SeriesMarkerBarPosition, SeriesMarkerBar, SeriesMarkerShape, Time } from 'lightweight-charts'
import { weekStartKey } from './weekly'
import type { AlertSignal, CompositeSignalEntry, SignalsResponse } from './api'

export type SourceKey = 'alert' | 'composite' | 'backtest'

export interface FlatSignal {
  id: string
  source: SourceKey
  date: string
  payload: AlertSignal | CompositeSignalEntry
}

type MarkerStyle = { shape: SeriesMarkerShape; position: SeriesMarkerBarPosition; color: string; size: number }

const SOURCE_STYLE: Record<SourceKey, MarkerStyle> = {
  alert: { shape: 'arrowUp', position: 'belowBar', color: '#3ddc97', size: 1 },
  composite: { shape: 'circle', position: 'aboveBar', color: '#8a7cff', size: 1 },
  backtest: { shape: 'square', position: 'aboveBar', color: '#5b6473', size: 0.75 },
}

/** Legacy alerts are a grab-bag of `alert_type`s (`BUY_SIGNAL`,
 * `PRICE_UP`, `RSI_OVERBOUGHT`, ...); only `BUY_SIGNAL` is an actual buy
 * call, so it keeps the green arrow. Everything else gets a visually
 * distinct, deliberately unremarkable style so it never reads as a buy
 * signal at a glance. */
export type AlertStyleKey = 'buy' | 'other'

export function alertStyleKey(alertType: string): AlertStyleKey {
  return alertType === 'BUY_SIGNAL' ? 'buy' : 'other'
}

const ALERT_STYLE: Record<AlertStyleKey, MarkerStyle> = {
  buy: SOURCE_STYLE.alert,
  other: { shape: 'circle', position: 'belowBar', color: '#8a93a6', size: 0.85 },
}

function styleFor(source: SourceKey, styleKey: string): MarkerStyle {
  if (source === 'alert') return ALERT_STYLE[styleKey as AlertStyleKey]
  return SOURCE_STYLE[source]
}

/** Flattens a SignalsResponse into one date-sortable list, independent of
 * chart mode. Pure and total: never throws, never drops a row by itself —
 * dropping happens later in buildMarkers, based on what candles exist. */
export function flattenSignals(response: SignalsResponse | null): FlatSignal[] {
  if (!response) return []
  const flat: FlatSignal[] = []
  for (const a of response.alerts) {
    flat.push({ id: `alert:${a.id}`, source: 'alert', date: a.triggered_at.slice(0, 10), payload: a })
  }
  for (const c of response.composite_signals) {
    flat.push({ id: `composite:${c.id}`, source: 'composite', date: c.signal_date, payload: c })
  }
  for (const b of response.backtest_signals) {
    flat.push({ id: `backtest:${b.id}`, source: 'backtest', date: b.signal_date, payload: b })
  }
  return flat.sort((x, y) => (x.date < y.date ? -1 : x.date > y.date ? 1 : 0))
}

/** Latest time in `candleTimes` (ascending, unique) that is <= `date`, or
 * null if `date` is before every loaded candle. This is how a signal date
 * (always a trading day, but not necessarily one the chart has loaded or
 * has a candle for - weekends included) gets snapped onto an existing
 * data point, since lightweight-charts requires a marker's time to match
 * one exactly. */
export function snapToCandle(date: string, candleTimes: string[]): string | null {
  let lo = 0
  let hi = candleTimes.length - 1
  let result: string | null = null
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (candleTimes[mid] <= date) {
      result = candleTimes[mid]
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return result
}

export interface BuildMarkersResult {
  markers: SeriesMarkerBar<Time>[]
  groups: Map<string, FlatSignal[]>
  droppedCount: number
}

/** Builds the marker list + click lookup for the currently enabled sources
 * and chart mode. `candleTimes` must be the ascending time keys of the
 * series actually rendered for that mode (daily candles, or the weekly
 * candles aggregateWeekly() produced) so every marker lands on a real
 * point. In weekly mode, multiple same-source signals landing in the same
 * week collapse into one marker whose text shows the count. */
export function buildMarkers(
  flat: FlatSignal[],
  mode: 'D' | 'W',
  enabled: Set<SourceKey>,
  candleTimes: string[],
): BuildMarkersResult {
  const groups = new Map<string, FlatSignal[]>()
  const groupTimes = new Map<string, string>()
  const groupStyleKeys = new Map<string, string>()
  let droppedCount = 0

  for (const signal of flat) {
    if (!enabled.has(signal.source)) continue
    const targetDate = mode === 'W' ? weekStartKey(signal.date) : signal.date
    const snapped = snapToCandle(targetDate, candleTimes)
    if (snapped === null) {
      droppedCount += 1
      continue
    }
    const styleKey = signal.source === 'alert' ? alertStyleKey((signal.payload as AlertSignal).alert_type) : signal.source
    const groupKey = `${signal.source}:${styleKey}:${snapped}`
    const group = groups.get(groupKey)
    if (group) {
      group.push(signal)
    } else {
      groups.set(groupKey, [signal])
      groupTimes.set(groupKey, snapped)
      groupStyleKeys.set(groupKey, styleKey)
    }
  }

  const markers: SeriesMarkerBar<Time>[] = []
  for (const [groupKey, group] of groups) {
    const style = styleFor(group[0].source, groupStyleKeys.get(groupKey) as string)
    markers.push({
      time: groupTimes.get(groupKey) as Time,
      position: style.position,
      shape: style.shape,
      color: style.color,
      size: style.size,
      id: groupKey,
      text: group.length > 1 ? String(group.length) : '',
    })
  }
  markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))

  return { markers, groups, droppedCount }
}

export const SOURCE_LABELS: Record<SourceKey, string> = {
  alert: 'Alerts',
  composite: 'Composite',
  backtest: 'Backtest',
}

export const SOURCE_COLORS: Record<SourceKey, string> = {
  alert: SOURCE_STYLE.alert.color,
  composite: SOURCE_STYLE.composite.color,
  backtest: SOURCE_STYLE.backtest.color,
}
