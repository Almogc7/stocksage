import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from 'lightweight-charts'
import { usePrices } from '@/hooks/usePrices'
import { sma } from '@/lib/sma'
import { formatPercent, formatPrice } from '@/lib/format'
import type { Candle, PriceRange, PricesResponse } from '@/lib/api'

const RANGES = [
  { key: '1mo', label: '1M', days: 30 },
  { key: '3mo', label: '3M', days: 90 },
  { key: '6mo', label: '6M', days: 182 },
  { key: '1y', label: '1Y', days: 365 },
  { key: '2y', label: '2Y', days: 730 },
  { key: '5y', label: '5Y', days: 1825 },
] as const

const SMA150_COLOR = '#e6ebf2'
const SMA200_COLOR = '#4f9cff'

function timeToKey(time: Time): string {
  if (typeof time === 'string') return time
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10)
  const { year, month, day } = time
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function daysBefore(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

interface OhlcReadout {
  time: string
  open: number
  high: number
  low: number
  close: number
  changePct: number
}

function readoutFromCandle(c: Candle): OhlcReadout {
  return {
    time: c.time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    changePct: ((c.close - c.open) / c.open) * 100,
  }
}

export function MaximizedChart({
  symbol,
  onClose,
}: {
  symbol: string
  onClose: () => void
}) {
  const { data, loading, error, fetchFiveYear } = usePrices(symbol)
  const [activeData, setActiveData] = useState<PricesResponse | null>(null)
  const [selectedRange, setSelectedRange] = useState<PriceRange>('1y')
  const [rangeLoading, setRangeLoading] = useState(false)
  const [hover, setHover] = useState<OhlcReadout | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  useEffect(() => {
    setActiveData(null)
    setSelectedRange('1y')
    setHover(null)
  }, [symbol])

  useEffect(() => {
    if (data) setActiveData(data)
  }, [data])

  useEffect(() => {
    if (!containerRef.current || !activeData || activeData.candles.length === 0) return

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
    candleSeries.setData(activeData.candles)
    candleSeriesRef.current = candleSeries

    const closeByTime = new Map(activeData.candles.map((c) => [c.time, c]))
    const volumeSeries = chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: 'volume' } },
      1,
    )
    volumeSeries.setData(
      activeData.volume.map((v) => {
        const candle = closeByTime.get(v.time)
        const up = candle ? candle.close >= candle.open : true
        return { ...v, color: up ? '#3ddc97' : '#ff5c7a' }
      }),
    )
    chart.panes()[1]?.setHeight(100)

    const closesForSma = activeData.candles.map((c) => ({ time: c.time, close: c.close }))
    const sma150Series = chart.addSeries(LineSeries, { color: SMA150_COLOR, lineWidth: 1 }, 0)
    sma150Series.setData(sma(closesForSma, 150))

    const sma200Series = chart.addSeries(LineSeries, { color: SMA200_COLOR, lineWidth: 1 }, 0)
    sma200Series.setData(sma(closesForSma, 200))

    const lastTime = activeData.candles.at(-1)!.time
    const firstTime = activeData.candles[0].time
    const rangeDef = RANGES.find((r) => r.key === selectedRange) ?? RANGES[3]
    const defaultFrom = daysBefore(lastTime, rangeDef.days)
    chart.timeScale().setVisibleRange({
      from: defaultFrom < firstTime ? firstTime : defaultFrom,
      to: lastTime,
    })

    setHover(readoutFromCandle(activeData.candles.at(-1)!))

    chart.subscribeCrosshairMove((param) => {
      if (!param.time) {
        setHover(readoutFromCandle(activeData.candles.at(-1)!))
        return
      }
      const candle = closeByTime.get(timeToKey(param.time))
      if (candle) setHover(readoutFromCandle(candle))
    })

    return () => {
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeData])

  async function selectRange(key: PriceRange, days: number) {
    setSelectedRange(key)
    const chart = chartRef.current

    if (key === '5y' && (!activeData || activeData.range !== '5y')) {
      setRangeLoading(true)
      try {
        const res = await fetchFiveYear()
        if (res) setActiveData(res)
      } finally {
        setRangeLoading(false)
      }
      return
    }

    if (!chart || !activeData || activeData.candles.length === 0) return
    const lastTime = activeData.candles.at(-1)!.time
    const firstTime = activeData.candles[0].time
    const from = daysBefore(lastTime, days)
    chart.timeScale().setVisibleRange({
      from: from < firstTime ? firstTime : from,
      to: lastTime,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[var(--bg)]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] p-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold">{symbol}</h2>
          <a
            href={`https://www.tradingview.com/symbols/${encodeURIComponent(symbol)}/`}
            target="_blank"
            rel="noreferrer"
            className="rounded-md border border-[var(--accent)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent)]/10"
          >
            Open in TradingView
          </a>
          <div className="flex items-center gap-3 text-xs text-[var(--text-dim)]">
            <span className="flex items-center gap-1">
              <span className="inline-block h-0.5 w-3" style={{ backgroundColor: SMA150_COLOR }} />
              SMA150
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-0.5 w-3" style={{ backgroundColor: SMA200_COLOR }} />
              SMA200
            </span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="rounded-md border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--panel-hover)]"
        >
          Close
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] p-2">
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r.key}
              onClick={() => selectRange(r.key, r.days)}
              className={
                selectedRange === r.key
                  ? 'rounded-md bg-[var(--accent)] px-2 py-1 text-xs font-medium text-[#0a0e14]'
                  : 'rounded-md px-2 py-1 text-xs text-[var(--text-dim)] hover:bg-[var(--panel-hover)] hover:text-[var(--text)]'
              }
            >
              {r.label}
            </button>
          ))}
        </div>

        {hover && (
          <div className="tabular flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-dim)]">
            <span>{hover.time}</span>
            <span>
              O <span className="text-[var(--text)]">{formatPrice(hover.open)}</span>
            </span>
            <span>
              H <span className="text-[var(--text)]">{formatPrice(hover.high)}</span>
            </span>
            <span>
              L <span className="text-[var(--text)]">{formatPrice(hover.low)}</span>
            </span>
            <span>
              C <span className="text-[var(--text)]">{formatPrice(hover.close)}</span>
            </span>
            <span className={hover.changePct >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'}>
              {formatPercent(hover.changePct)}
            </span>
          </div>
        )}
      </div>

      <div className="relative flex-1">
        {(loading || rangeLoading) && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[var(--bg)]/60 text-[var(--text-dim)]">
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
