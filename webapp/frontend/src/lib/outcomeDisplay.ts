import type { SignalOutcome } from './api'
import { formatPrice } from './format'

export interface OutcomeRow {
  label: string
  value: string
}

/** Decides how to render a signal's outcome. Returns null only when
 * nothing at all is known yet (no outcome row joined), in which case the
 * caller should show a blanket "Outcome pending" message. Otherwise
 * returns one row per known/unknown field, so partial data (e.g. a stop
 * already hit but T+5/T+10 not yet computed) is shown instead of being
 * hidden behind the aggregate `pending` flag. */
export function describeOutcome(outcome: SignalOutcome): OutcomeRow[] | null {
  const noDataAtAll =
    outcome.close_t1 == null &&
    outcome.close_t3 == null &&
    outcome.close_t5 == null &&
    outcome.close_t10 == null &&
    outcome.first_barrier_hit == null

  if (noDataAtAll) return null

  const rows: OutcomeRow[] = [
    { label: 'T+1', value: outcome.close_t1 != null ? formatPrice(outcome.close_t1) : 'pending' },
    { label: 'T+3', value: outcome.close_t3 != null ? formatPrice(outcome.close_t3) : 'pending' },
    { label: 'T+5', value: outcome.close_t5 != null ? formatPrice(outcome.close_t5) : 'pending' },
    { label: 'T+10', value: outcome.close_t10 != null ? formatPrice(outcome.close_t10) : 'pending' },
  ]

  if (outcome.first_barrier_hit != null) {
    rows.push({ label: 'Stop', value: outcome.first_barrier_hit === 'stop_loss' ? 'hit' : 'not hit' })
  }

  if (outcome.r_multiple != null) {
    rows.push({ label: 'R', value: outcome.r_multiple.toFixed(2) })
  }

  return rows
}
