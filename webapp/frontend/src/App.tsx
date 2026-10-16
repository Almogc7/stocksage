import { useMemo, useState } from 'react'
import { Filters, type SortOption } from '@/components/Filters'
import { MaximizedChart } from '@/components/MaximizedChart'
import { StockGrid } from '@/components/StockGrid'
import { SparklineProvider } from '@/hooks/useSparklines'
import { useWatchlist } from '@/hooks/useWatchlist'

const SPARKLINE_RANGE = '3mo'

function LoadingGrid() {
  return (
    <div className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {Array.from({ length: 10 }).map((_, i) => (
        <div
          key={i}
          className="flex h-[124px] flex-col gap-2 rounded-xl border border-[var(--border)] bg-[var(--panel)] p-3"
        >
          <div className="h-4 w-16 animate-pulse rounded bg-[var(--panel-hover)]" />
          <div className="h-3 w-24 animate-pulse rounded bg-[var(--panel-hover)]" />
          <div className="h-9 w-full animate-pulse rounded bg-[var(--panel-hover)]" />
          <div className="h-4 w-full animate-pulse rounded bg-[var(--panel-hover)]" />
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const { items, loading, error, fetchedAt, refetch } = useWatchlist()
  const [tier, setTier] = useState('ACTIVE_AND_PINNED')
  const [category, setCategory] = useState('ALL')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortOption>('name')
  const [openSymbol, setOpenSymbol] = useState<string | null>(null)

  const categories = useMemo(
    () => Array.from(new Set(items.map((i) => i.category))).sort(),
    [items],
  )

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (tier === 'ACTIVE_AND_PINNED') {
        if (item.wl_state !== 'ACTIVE' && !item.pinned) return false
      } else if (tier !== 'ALL' && item.wl_state !== tier) {
        return false
      }
      if (category !== 'ALL' && item.category !== category) return false
      if (search && !item.symbol.toLowerCase().includes(search.trim().toLowerCase())) {
        return false
      }
      return true
    })
  }, [items, tier, category, search])

  return (
    <div className="min-h-screen">
      <header className="flex items-baseline justify-between border-b border-[var(--border)] p-3">
        <h1 className="text-lg font-semibold">StockSage</h1>
        {!loading && !error && fetchedAt && (
          <span className="text-xs text-[var(--text-dim)]">
            Data as of {fetchedAt.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })}
          </span>
        )}
      </header>

      <Filters
        categories={categories}
        category={category}
        onCategoryChange={setCategory}
        tier={tier}
        onTierChange={setTier}
        search={search}
        onSearchChange={setSearch}
        sort={sort}
        onSortChange={setSort}
      />

      {loading && <LoadingGrid />}

      {error && (
        <div className="flex flex-col items-center gap-2 p-6 text-center">
          <span className="text-[var(--down)]">{error}</span>
          <button
            onClick={refetch}
            className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--panel-hover)]"
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && (
        <SparklineProvider range={SPARKLINE_RANGE}>
          <StockGrid items={filtered} onOpen={setOpenSymbol} sort={sort} />
        </SparklineProvider>
      )}

      {openSymbol && (
        <MaximizedChart symbol={openSymbol} onClose={() => setOpenSymbol(null)} />
      )}
    </div>
  )
}
