import type { AlertSignal, CompositeSignalEntry, SignalOutcome } from '@/lib/api'
import type { FlatSignal } from '@/lib/signalMarkers'
import { formatPrice } from '@/lib/format'
import { describeOutcome } from '@/lib/outcomeDisplay'

function isAlert(signal: FlatSignal): signal is FlatSignal & { payload: AlertSignal } {
  return signal.source === 'alert'
}

function OutcomeBlock({ outcome }: { outcome: SignalOutcome }) {
  const rows = describeOutcome(outcome)
  if (rows === null) {
    return <div className="text-[var(--text-dim)]">Outcome pending</div>
  }
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1">
      {rows.map((r) => (
        <span key={r.label}>
          {r.label} {r.value}
        </span>
      ))}
    </div>
  )
}

function SignalRow({ signal }: { signal: FlatSignal }) {
  if (isAlert(signal)) {
    const a = signal.payload
    return (
      <div className="space-y-1 border-b border-[var(--border)] py-2 text-xs last:border-0">
        <div className="flex items-center justify-between">
          <span className="font-medium text-[var(--text)]">{signal.date}</span>
          <span className="text-[var(--text-dim)]">
            Flagged (legacy, {a.alert_type}) — score {a.score ?? '—'}, verdict {a.verdict ?? '—'}
          </span>
        </div>
        <div className="text-[var(--text-dim)]">
          Price at flag {a.price_at_alert != null ? formatPrice(a.price_at_alert) : '—'}
          {a.stop_loss != null && <> · Stop {formatPrice(a.stop_loss)}</>}
          {a.take_profit != null && <> · Target {formatPrice(a.take_profit)}</>}
        </div>
        {a.triggered_signals.length > 0 && (
          <div className="text-[var(--text-dim)]">Signals: {a.triggered_signals.join(', ')}</div>
        )}
        <OutcomeBlock outcome={a.outcome} />
      </div>
    )
  }

  const c = signal.payload as CompositeSignalEntry
  const label = signal.source === 'backtest' ? 'Flagged (backtest sample)' : 'Flagged (composite)'
  return (
    <div className="space-y-1 border-b border-[var(--border)] py-2 text-xs last:border-0">
      <div className="flex items-center justify-between">
        <span className="font-medium text-[var(--text)]">{signal.date}</span>
        <span className="text-[var(--text-dim)]">
          {label} — score {c.total_score.toFixed(1)}/{c.required_score}, regime {c.regime}
        </span>
      </div>
      <div className="text-[var(--text-dim)]">
        Trend {c.trend_pts.toFixed(0)} · Momentum {c.momentum_pts.toFixed(0)} · Volume{' '}
        {c.volume_pts.toFixed(0)} · RS {c.rs_pts.toFixed(0)}
        {c.rs_ratio != null && ` (ratio ${c.rs_ratio.toFixed(2)}, required ${c.required_rs.toFixed(2)})`}
      </div>
      <div className="text-[var(--text-dim)]">
        Price {formatPrice(c.price)}
        {c.stop_price != null && <> · Stop {formatPrice(c.stop_price)}</>}
      </div>
      {signal.source === 'backtest' && (
        <div className="text-[var(--down)]">
          Composite scores did not discriminate winners in the 2-year backtest — shown for
          reference only.
        </div>
      )}
      <OutcomeBlock outcome={c.outcome} />
    </div>
  )
}

export function SignalDetailPanel({
  signals,
  onClose,
}: {
  signals: FlatSignal[]
  onClose: () => void
}) {
  return (
    <div className="absolute right-3 top-3 z-20 max-h-[70%] w-80 overflow-y-auto rounded-md border border-[var(--border)] bg-[var(--bg)] p-3 shadow-lg">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--text-dim)]">
          {signals.length > 1 ? `${signals.length} signals` : 'Signal'}
        </span>
        <button
          onClick={onClose}
          className="rounded px-1 text-xs text-[var(--text-dim)] hover:bg-[var(--panel-hover)] hover:text-[var(--text)]"
        >
          ✕
        </button>
      </div>
      {signals.map((s) => (
        <SignalRow key={s.id} signal={s} />
      ))}
    </div>
  )
}
