import { describe, expect, it } from 'vitest'
import { alertStyleKey, buildMarkers, flattenSignals, snapToCandle, type FlatSignal } from './signalMarkers'
import type { AlertSignal, SignalsResponse } from './api'

const NO_OUTCOME = {
  pending: true,
  close_t1: null,
  close_t3: null,
  close_t5: null,
  close_t10: null,
  max_adverse_excursion: null,
  first_barrier_hit: null,
  r_multiple: null,
}

function emptyResponse(): SignalsResponse {
  return {
    symbol: 'AAPL',
    from: null,
    alerts: [],
    composite_signals: [],
    backtest_run_id: null,
    backtest_signals: [],
  }
}

function composite(id: number, signal_date: string) {
  return {
    id,
    signal_date,
    regime: 'bull',
    total_score: 80,
    required_score: 70,
    trend_pts: 20,
    momentum_pts: 20,
    volume_pts: 20,
    rs_pts: 20,
    rs_ratio: 1.1,
    required_rs: 1.0,
    price: 150,
    atr: 2,
    stop_price: 145,
    stop_multiplier: 2.5,
    outcome: NO_OUTCOME,
  }
}

function alert(id: number, triggered_at: string, alert_type: string): AlertSignal {
  return {
    id,
    triggered_at,
    alert_type,
    message: 'm',
    score: null,
    verdict: null,
    price_at_alert: null,
    rsi: null,
    atr: null,
    stop_loss: null,
    take_profit: null,
    triggered_signals: [],
    outcome: NO_OUTCOME,
  }
}

describe('alertStyleKey', () => {
  it('is "buy" only for BUY_SIGNAL', () => {
    expect(alertStyleKey('BUY_SIGNAL')).toBe('buy')
  })

  it('is "other" for any non-BUY_SIGNAL alert_type', () => {
    expect(alertStyleKey('PRICE_UP')).toBe('other')
    expect(alertStyleKey('RSI_OVERBOUGHT')).toBe('other')
  })
})

describe('snapToCandle', () => {
  const candleTimes = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08', '2026-01-09']

  it('snaps a weekend date to the preceding trading-day candle', () => {
    // 2026-01-10 and 2026-01-11 are a Sat/Sun with no candle; the last
    // trading day on or before it is 2026-01-09 (Friday).
    expect(snapToCandle('2026-01-11', candleTimes)).toBe('2026-01-09')
  })

  it('returns the exact match when the date has a candle', () => {
    expect(snapToCandle('2026-01-07', candleTimes)).toBe('2026-01-07')
  })

  it('returns null when the date is before every loaded candle', () => {
    expect(snapToCandle('2025-12-01', candleTimes)).toBeNull()
  })
})

describe('buildMarkers', () => {
  const candleTimes = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08', '2026-01-09']

  it('drops a pre-range signal and reports the count', () => {
    const response = emptyResponse()
    response.composite_signals = [composite(1, '2025-12-01')]
    const flat = flattenSignals(response)
    const result = buildMarkers(flat, 'D', new Set(['composite']), candleTimes)
    expect(result.markers).toHaveLength(0)
    expect(result.droppedCount).toBe(1)
  })

  it('snaps a weekend composite signal onto the preceding daily candle', () => {
    const response = emptyResponse()
    response.composite_signals = [composite(1, '2026-01-11')]
    const flat = flattenSignals(response)
    const result = buildMarkers(flat, 'D', new Set(['composite']), candleTimes)
    expect(result.markers).toHaveLength(1)
    expect(result.markers[0].time).toBe('2026-01-09')
    expect(result.droppedCount).toBe(0)
  })

  it('collapses multiple same-week, same-source signals into one counted marker', () => {
    const weeklyTimes = ['2025-12-29', '2026-01-05'] // Monday week-start keys
    const response = emptyResponse()
    response.composite_signals = [
      composite(1, '2026-01-05'),
      composite(2, '2026-01-06'),
      composite(3, '2026-01-07'),
    ]
    const flat = flattenSignals(response)
    const result = buildMarkers(flat, 'W', new Set(['composite']), weeklyTimes)
    expect(result.markers).toHaveLength(1)
    expect(result.markers[0].text).toBe('3')
    const group = result.groups.get(result.markers[0].id as string) as FlatSignal[]
    expect(group).toHaveLength(3)
  })

  it('keeps different sources in the same week as separate markers', () => {
    const weeklyTimes = ['2026-01-05']
    const response = emptyResponse()
    response.composite_signals = [composite(1, '2026-01-06')]
    response.alerts = [
      {
        id: 1,
        triggered_at: '2026-01-07 14:00:00',
        alert_type: 'BUY_SIGNAL',
        message: 'm',
        score: 80,
        verdict: 'BUY',
        price_at_alert: 150,
        rsi: 55,
        atr: 2,
        stop_loss: 145,
        take_profit: 160,
        triggered_signals: [],
        outcome: NO_OUTCOME,
      },
    ]
    const flat = flattenSignals(response)
    const result = buildMarkers(flat, 'W', new Set(['composite', 'alert']), weeklyTimes)
    expect(result.markers).toHaveLength(2)
  })

  it('excludes disabled sources entirely', () => {
    const response = emptyResponse()
    response.composite_signals = [composite(1, '2026-01-05')]
    const flat = flattenSignals(response)
    const result = buildMarkers(flat, 'D', new Set(['alert']), candleTimes)
    expect(result.markers).toHaveLength(0)
    expect(result.droppedCount).toBe(0)
  })

  it('splits a BUY_SIGNAL and a non-BUY_SIGNAL alert on the same day into two markers', () => {
    const response = emptyResponse()
    response.alerts = [
      alert(1, '2026-01-07 13:00:00', 'BUY_SIGNAL'),
      alert(2, '2026-01-07 14:00:00', 'PRICE_UP'),
    ]
    const flat = flattenSignals(response)
    const result = buildMarkers(flat, 'D', new Set(['alert']), candleTimes)
    expect(result.markers).toHaveLength(2)
    const shapes = result.markers.map((m) => m.shape).sort()
    expect(shapes).toEqual(['arrowUp', 'circle'])
    const colors = result.markers.map((m) => m.color).sort()
    expect(colors).toEqual(['#3ddc97', '#8a93a6'])
  })

  it('still collapses two non-BUY_SIGNAL alerts on the same day into one neutral marker', () => {
    // Regression guard for the FSLR case: two legacy alerts (neither a buy
    // signal) land on the same day and should collapse into one "2"
    // marker, styled neutral rather than the green buy arrow.
    const response = emptyResponse()
    response.alerts = [
      alert(1, '2026-01-07 13:00:00', 'PRICE_UP'),
      alert(2, '2026-01-07 14:00:00', 'RSI_OVERBOUGHT'),
    ]
    const flat = flattenSignals(response)
    const result = buildMarkers(flat, 'D', new Set(['alert']), candleTimes)
    expect(result.markers).toHaveLength(1)
    expect(result.markers[0].text).toBe('2')
    expect(result.markers[0].shape).toBe('circle')
    expect(result.markers[0].color).toBe('#8a93a6')
  })
})
