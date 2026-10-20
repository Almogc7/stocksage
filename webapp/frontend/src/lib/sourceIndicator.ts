export type IndicatorColorVar = '--up' | '--down' | '--text-dim'

export interface SourceIndicatorState {
  /** true = on (filled dot), false = off (hollow dot) */
  filled: boolean
  colorVar: IndicatorColorVar
  /** backtest fetch in flight */
  pulsing: boolean
  /** short error message, only set on error */
  tooltip: string | null
  /** e.g. "(4)", or null when not yet known */
  countLabel: string | null
}

/** Maps a source toggle's raw state (on/off, loading, error, signal count)
 * to how its status dot and label should render. Precedence is
 * error > loading > settled, since loading/erroring only happen once a
 * source has been turned on — `filled` stays tied to `enabled` throughout. */
export function describeSourceIndicator(opts: {
  enabled: boolean
  count: number | null
  loading?: boolean
  error?: string | null
}): SourceIndicatorState {
  const loading = opts.loading ?? false
  const error = opts.error ?? null

  if (error) {
    return { filled: opts.enabled, colorVar: '--down', pulsing: false, tooltip: error, countLabel: null }
  }
  if (loading) {
    return { filled: opts.enabled, colorVar: '--text-dim', pulsing: true, tooltip: null, countLabel: null }
  }
  const isEmpty = opts.enabled && opts.count === 0
  return {
    filled: opts.enabled,
    colorVar: isEmpty ? '--text-dim' : opts.enabled ? '--up' : '--down',
    pulsing: false,
    tooltip: null,
    countLabel: opts.count != null ? `(${opts.count})` : null,
  }
}
