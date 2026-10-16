import { useMemo, useState } from 'react'
import { Filters } from '@/components/Filters'
import { MaximizedChart } from '@/components/MaximizedChart'
import { StockGrid } from '@/components/StockGrid'
import { SparklineProvider } from '@/hooks/useSparklines'
import { useWatchlist } from '@/hooks/useWatchlist'

const SPARKLINE_RANGE = '3mo'

export default function App() {
  const { items, loading, error } = useWatchlist()
  const [tier, setTier] = useState('ACTIVE_AND_PINNED')
  const [category, setCategory] = useState('ALL')
  const [search, setSearch] = useState('')
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
      <header className="border-b border-[var(--border)] p-3">
        <h1 className="text-lg font-semibold">StockSage</h1>
      </header>

      <Filters
        categories={categories}
        category={category}
        onCategoryChange={setCategory}
        tier={tier}
        onTierChange={setTier}
        search={search}
        onSearchChange={setSearch}
      />

      {loading && <div className="p-6 text-center text-[var(--text-dim)]">Loading…</div>}
      {error && <div className="p-6 text-center text-[var(--down)]">{error}</div>}

      {!loading && !error && (
        <SparklineProvider range={SPARKLINE_RANGE}>
          <StockGrid items={filtered} onOpen={setOpenSymbol} />
        </SparklineProvider>
      )}

      {openSymbol && (
        <MaximizedChart symbol={openSymbol} onClose={() => setOpenSymbol(null)} />
      )}
    </div>
  )
}
