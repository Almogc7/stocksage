import { useEffect, useMemo } from 'react'
import type { WatchlistItem } from '@/lib/api'
import type { SortOption } from './Filters'
import { useSparklineRegistry } from '@/hooks/useSparklines'
import { sparklinePct } from '@/lib/sparklinePct'
import { StockCard } from './StockCard'

/** Above this many filtered symbols, sorting by change would mean eagerly
 * fetching sparklines for all of them beyond the provider's one-chunk batch
 * — so change-sort is disabled and the grid falls back to name order. */
const MAX_EAGER_SORT_SYMBOLS = 60

export function StockGrid({
  items,
  onOpen,
  sort,
}: {
  items: WatchlistItem[]
  onOpen: (symbol: string) => void
  sort: SortOption
}) {
  const { results, request } = useSparklineRegistry()
  const changeSortDisabled = items.length > MAX_EAGER_SORT_SYMBOLS
  const effectiveSort = sort === 'change_3m' && changeSortDisabled ? 'name' : sort

  useEffect(() => {
    if (effectiveSort !== 'change_3m') return
    for (const item of items) {
      request(item.symbol)
    }
  }, [effectiveSort, items, request])

  const sortedItems = useMemo(() => {
    const copy = [...items]
    if (effectiveSort === 'change_3m') {
      copy.sort((a, b) => {
        const pctA = sparklinePct(results[a.symbol])
        const pctB = sparklinePct(results[b.symbol])
        if (pctA === null && pctB === null) return a.symbol.localeCompare(b.symbol)
        if (pctA === null) return 1
        if (pctB === null) return -1
        return pctB - pctA
      })
    } else {
      copy.sort((a, b) => a.symbol.localeCompare(b.symbol))
    }
    return copy
  }, [items, effectiveSort, results])

  return (
    <div>
      {sort === 'change_3m' && changeSortDisabled && (
        <div className="px-3 pb-2 text-xs text-[var(--text-dim)]">
          Narrow the filter to {MAX_EAGER_SORT_SYMBOLS} symbols or fewer to sort by change.
          Showing name order instead.
        </div>
      )}

      {items.length === 0 ? (
        <div className="p-6 text-center text-sm text-[var(--text-dim)]">
          No symbols match the current filters.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {sortedItems.map((item) => (
            <StockCard key={item.symbol} item={item} onOpen={onOpen} />
          ))}
        </div>
      )}
    </div>
  )
}
