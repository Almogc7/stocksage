import { describe, expect, it } from 'vitest'
import { describeOutcome } from './outcomeDisplay'
import type { SignalOutcome } from './api'

function outcome(overrides: Partial<SignalOutcome> = {}): SignalOutcome {
  return {
    pending: true,
    close_t1: null,
    close_t3: null,
    close_t5: null,
    close_t10: null,
    max_adverse_excursion: null,
    first_barrier_hit: null,
    r_multiple: null,
    ...overrides,
  }
}

describe('describeOutcome', () => {
  it('returns null when nothing is known yet (no outcome row joined)', () => {
    expect(describeOutcome(outcome())).toBeNull()
  })

  it('shows known values and labels unresolved T+N fields "pending" when a stop has already hit', () => {
    // Shape of SCCO's 2026-10-12 composite signal: stop already hit, T+1/T+3
    // known, T+5/T+10 not yet computed.
    const rows = describeOutcome(
      outcome({
        pending: true,
        close_t1: 207.42,
        close_t3: 201.69,
        first_barrier_hit: 'stop_loss',
        r_multiple: -1.0,
      }),
    )
    expect(rows).not.toBeNull()
    expect(rows).toEqual([
      { label: 'T+1', value: '207.42' },
      { label: 'T+3', value: '201.69' },
      { label: 'T+5', value: 'pending' },
      { label: 'T+10', value: 'pending' },
      { label: 'Stop', value: 'touched intraday' },
      { label: 'R', value: '-1.00' },
    ])
  })

  it('renders a fully-resolved outcome with every field populated, neither barrier touched', () => {
    const rows = describeOutcome(
      outcome({
        pending: false,
        close_t1: 202.63,
        close_t3: 202.64,
        close_t5: 205.54,
        close_t10: 209.21,
        first_barrier_hit: 'none',
        r_multiple: 0.31,
      }),
    )
    expect(rows).toEqual([
      { label: 'T+1', value: '202.63' },
      { label: 'T+3', value: '202.64' },
      { label: 'T+5', value: '205.54' },
      { label: 'T+10', value: '209.21' },
      { label: 'Barrier', value: 'Neither stop nor target touched in the window' },
      { label: 'R', value: '0.31' },
    ])
  })

  it('labels a take-profit exit as "Target touched first", not a stop label', () => {
    const rows = describeOutcome(
      outcome({
        pending: false,
        close_t1: 160.0,
        close_t3: 165.0,
        close_t5: 170.0,
        close_t10: 172.0,
        first_barrier_hit: 'take_profit',
        r_multiple: 2.0,
      }),
    )
    expect(rows).toContainEqual({ label: 'Target', value: 'touched first' })
    expect(rows).not.toContainEqual(expect.objectContaining({ label: 'Stop' }))
  })
})
