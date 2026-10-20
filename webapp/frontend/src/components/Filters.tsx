import type { WlState } from '@/lib/api'

const WL_STATE_OPTIONS: { value: WlState | 'ACTIVE_AND_PINNED' | 'ALL'; label: string }[] = [
  { value: 'ACTIVE_AND_PINNED', label: 'Active (default)' },
  { value: 'ALL', label: 'All tiers' },
  { value: 'MONITOR', label: 'Monitor' },
  { value: 'ETF_INDEX_CONTEXT', label: 'ETF / Index' },
  { value: 'TEMPORARILY_INELIGIBLE', label: 'Ineligible' },
  { value: 'USER_REMOVED', label: 'Removed' },
]

export type SortOption = 'name' | 'change_3m'

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: 'name', label: 'Sort: Name' },
  { value: 'change_3m', label: 'Sort: 3M change' },
]

const baseSelect =
  'min-h-11 rounded-md border border-[var(--border)] bg-[var(--panel)] px-2 py-1.5 text-sm text-[var(--text)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]'

export function Filters({
  categories,
  category,
  onCategoryChange,
  tier,
  onTierChange,
  search,
  onSearchChange,
  sort,
  onSortChange,
}: {
  categories: string[]
  category: string
  onCategoryChange: (v: string) => void
  tier: string
  onTierChange: (v: string) => void
  search: string
  onSearchChange: (v: string) => void
  sort: SortOption
  onSortChange: (v: SortOption) => void
}) {
  return (
    <div className="flex flex-col gap-2 p-3">
      <input
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder="Search symbol…"
        className={`${baseSelect} w-full sm:w-64`}
      />
      <div className="flex flex-wrap gap-2">
        <select
          value={tier}
          onChange={(e) => onTierChange(e.target.value)}
          className={`${baseSelect} min-w-0 flex-1 sm:flex-none`}
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
          className={`${baseSelect} min-w-0 flex-1 sm:flex-none`}
        >
          <option value="ALL">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          value={sort}
          onChange={(e) => onSortChange(e.target.value as SortOption)}
          className={`${baseSelect} min-w-0 flex-1 sm:flex-none`}
        >
          {SORT_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
