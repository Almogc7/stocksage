import { useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
} from 'lightweight-charts'
import { usePrices } from '@/hooks/usePrices'
import { sma } from '@/lib/sma'

const RANGES = [
  { key: '1mo', label: '1M', days: 30 },
  { key: '3mo', label: '3M', days: 90 },
  { key: '6mo', label: '6M', days: 182 },
  { key: '1y', label: '1Y', days: 365 },
  { key: '2y', label: '2Y', days: 730 },
  { key: '5y', label: '5Y', days: 1825 },
] as const

function daysBefore(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

export function MaximizedChart({
  symbol,
  onClose,
}: {
  symbol: string
  onClose: () => void
}) {
  const { data, loading, error } = usePrices(symbol)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current || !data || data.candles.length === 0) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#0a0e14' },
        textColor: '#8b96a5',
      },
      grid: {
        vertLines: { color: '#1f2733' },
        horzLines: { color: '#1f2733' },
      },
      timeScale: { borderColor: '#1f2733' },
      rightPriceScale: { borderColor: '#1f2733' },
      autoSize: true,
    })
    chartRef.current = chart

    const candleSeries = chart.addSeries(CandlestickSeries, {}, 0)
    candleSeries.setData(data.candles)

    const volumeSeries = chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: 'volume' }, color: '#4f9cff' },
      1,
    )
    volumeSeries.setData(data.volume)
    chart.panes()[1]?.setHeight(100)

    const closesForSma = data.candles.map((c) => ({ time: c.time, close: c.close }))
    const sma150Series = chart.addSeries(LineSeries, { color: '#e6ebf2', lineWidth: 1 }, 0)
    sma150Series.setData(sma(closesForSma, 150))

    const sma200Series = chart.addSeries(LineSeries, { color: '#4f9cff', lineWidth: 1 }, 0)
    sma200Series.setData(sma(closesForSma, 200))

    const lastTime = data.candles.at(-1)!.time
    const firstTime = data.candles[0].time
    const defaultFrom = daysBefore(lastTime, 365)
    chart.timeScale().setVisibleRange({
      from: defaultFrom < firstTime ? firstTime : defaultFrom,
      to: lastTime,
    })

    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [data])

  function selectRange(days: number) {
    const chart = chartRef.current
    if (!chart || !data || data.candles.length === 0) return
    const lastTime = data.candles.at(-1)!.time
    const firstTime = data.candles[0].time
    const from = daysBefore(lastTime, days)
    chart.timeScale().setVisibleRange({
      from: from < firstTime ? firstTime : from,
      to: lastTime,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[var(--bg)]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] p-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">{symbol}</h2>
          <a
            href={`https://www.tradingview.com/symbols/${encodeURIComponent(symbol)}/`}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-[var(--accent)] underline"
          >
            Open in TradingView
          </a>
        </div>
        <button
          onClick={onClose}
          className="rounded-md border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--panel-hover)]"
        >
          Close
        </button>
      </div>

      <div className="flex gap-1 border-b border-[var(--border)] p-2">
        {RANGES.map((r) => (
          <button
            key={r.key}
            onClick={() => selectRange(r.days)}
            className="rounded-md px-2 py-1 text-xs text-[var(--text-dim)] hover:bg-[var(--panel-hover)] hover:text-[var(--text)]"
          >
            {r.label}
          </button>
        ))}
      </div>

      <div className="relative flex-1">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center text-[var(--text-dim)]">
            Loading…
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center text-[var(--down)]">
            {error}
          </div>
        )}
        <div ref={containerRef} className="h-full w-full" />
      </div>
    </div>
  )
}
