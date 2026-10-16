import { useEffect } from 'react'
import { useInView } from '@/hooks/useInView'
import { useSparklineRegistry } from '@/hooks/useSparklines'
import { formatPercent, formatPrice, rangeLabel } from '@/lib/format'
import { sparklineCloses, sparklinePct } from '@/lib/sparklinePct'
import type { SmaPosition, WatchlistItem } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Sparkline } from './Sparkline'

function SmaChip({ label, pos }: { label: string; pos: SmaPosition }) {
  return (
    <span
      className={cn(
        'rounded border px-1 py-0.5 text-[10px] leading-none',
        pos === 'above' && 'border-[var(--up)]/40 text-[var(--up)]',
        pos === 'below' && 'border-[var(--down)]/40 text-[var(--down)]',
        pos == null && 'border-[var(--border)] text-[var(--text-dim)]',
      )}
      title={
        pos == null
          ? `Not enough history for SMA${label}`
          : `Last close is ${pos} its SMA${label}`
      }
    >
      {pos === 'above' ? '▲' : pos === 'below' ? '▼' : '–'}
      {label}
    </span>
  )
}

export function StockCard({
  item,
  onOpen,
}: {
  item: WatchlistItem
  onOpen: (symbol: string) => void
}) {
  const { ref, inView } = useInView<HTMLButtonElement>()
  const { results, request, range } = useSparklineRegistry()

  useEffect(() => {
    if (inView) request(item.symbol)
  }, [inView, item.symbol, request])

  const state = results[item.symbol]

  const closes = sparklineCloses(state)
  const lastPrice = closes.at(-1)
  const pct = sparklinePct(state)
  const sma150 = state && state !== 'loading' ? state.sma150_pos : null
  const sma200 = state && state !== 'loading' ? state.sma200_pos : null

  return (
    <button
      ref={ref}
      onClick={() => onOpen(item.symbol)}
      className={cn(
        'flex flex-col gap-2 rounded-xl border bg-[var(--panel)] p-3 text-left',
        'transition-colors hover:bg-[var(--panel-hover)] hover:border-[var(--accent)]',
        'focus-visible:outline-2 focus-visible:outline-[var(--accent)]',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1 font-semibold text-[var(--text)]">
            {item.pinned && <span title="Pinned">📌</span>}
            {item.symbol}
          </div>
          <div className="text-xs text-[var(--text-dim)]">{item.category}</div>
        </div>
        <div className="flex shrink-0 gap-1">
          <SmaChip label="150" pos={sma150} />
          <SmaChip label="200" pos={sma200} />
        </div>
      </div>

      <div className="flex h-9 w-full items-center">
        {state === 'loading' || state === undefined ? (
          <div className="h-5 w-full animate-pulse rounded bg-[var(--panel-hover)]" />
        ) : closes.length >= 2 ? (
          <Sparkline closes={closes} height={36} />
        ) : (
          <span className="text-xs text-[var(--text-dim)]">No data</span>
        )}
      </div>

      <div className="tabular flex items-baseline justify-between text-sm">
        <span className="text-[var(--text)]">
          {lastPrice !== undefined ? formatPrice(lastPrice) : '—'}
        </span>
        {pct !== null && (
          <span className={pct >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'}>
            {rangeLabel(range)} {formatPercent(pct)}
          </span>
        )}
      </div>
    </button>
  )
}
