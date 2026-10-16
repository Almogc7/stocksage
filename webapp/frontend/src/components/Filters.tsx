import type { WlState } from '@/lib/api'

const WL_STATE_OPTIONS: { value: WlState | 'ACTIVE_AND_PINNED' | 'ALL'; label: string }[] = [
  { value: 'ACTIVE_AND_PINNED', label: 'Active (default)' },
  { value: 'ALL', label: 'All tiers' },
  { value: 'MONITOR', label: 'Monitor' },
  { value: 'ETF_INDEX_CONTEXT', label: 'ETF / Index' },
  { value: 'TEMPORARILY_INELIGIBLE', label: 'Ineligible' },
  { value: 'USER_REMOVED', label: 'Removed' },
]

const baseSelect =
  'rounded-md border border-[var(--border)] bg-[var(--panel)] px-2 py-1.5 text-sm text-[var(--text)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]'

export function Filters({
  categories,
  category,
  onCategoryChange,
  tier,
  onTierChange,
  search,
  onSearchChange,
}: {
  categories: string[]
  category: string
  onCategoryChange: (v: string) => void
  tier: string
  onTierChange: (v: string) => void
  search: string
  onSearchChange: (v: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 p-3">
      <input
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder="Search symbol…"
        className={`${baseSelect} min-w-0 flex-1 sm:flex-none sm:w-48`}
      />
      <select
        value={tier}
        onChange={(e) => onTierChange(e.target.value)}
        className={baseSelect}
      >
        {WL_STATE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      <select
        value={category}
        onChange={(e) => onCategoryChange(e.target.value)}
        className={baseSelect}
      >
        <option value="ALL">All categories</option>
        {categories.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </div>
  )
}
