import { useEffect } from 'react'
import { useInView } from '@/hooks/useInView'
import { useSparklineRegistry } from '@/hooks/useSparklines'
import { formatPercent, formatPrice, rangeLabel } from '@/lib/format'
import type { WatchlistItem } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Sparkline } from './Sparkline'

const STATE_LABEL: Record<string, string> = {
  ACTIVE: 'Active',
  MONITOR: 'Monitor',
  ETF_INDEX_CONTEXT: 'ETF/Index',
  TEMPORARILY_INELIGIBLE: 'Ineligible',
  USER_REMOVED: 'Removed',
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

  const closes = state && state !== 'loading' ? state.closes.map((c) => c.close) : []
  const lastPrice = closes.at(-1)
  const pct =
    closes.length >= 2 ? ((closes.at(-1)! - closes[0]) / closes[0]) * 100 : null

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
        <span className="shrink-0 rounded-md border border-[var(--border)] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[var(--text-dim)]">
          {STATE_LABEL[item.wl_state] ?? item.wl_state}
        </span>
      </div>

      <div className="flex h-9 items-center justify-center">
        {state === 'loading' || state === undefined ? (
          <div className="h-5 w-full animate-pulse rounded bg-[var(--panel-hover)]" />
        ) : closes.length >= 2 ? (
          <Sparkline closes={closes} width={140} height={36} />
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
