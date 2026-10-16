import type { WatchlistItem } from '@/lib/api'
import { StockCard } from './StockCard'

export function StockGrid({
  items,
  onOpen,
}: {
  items: WatchlistItem[]
  onOpen: (symbol: string) => void
}) {
  if (items.length === 0) {
    return (
      <div className="p-6 text-center text-sm text-[var(--text-dim)]">
        No symbols match the current filters.
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {items.map((item) => (
        <StockCard key={item.symbol} item={item} onOpen={onOpen} />
      ))}
    </div>
  )
}
