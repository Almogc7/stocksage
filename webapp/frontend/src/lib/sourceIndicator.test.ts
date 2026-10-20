import { describe, expect, it } from 'vitest'
import { describeSourceIndicator } from './sourceIndicator'

describe('describeSourceIndicator', () => {
  it('shows a filled green dot with a count label when enabled and settled', () => {
    expect(describeSourceIndicator({ enabled: true, count: 4 })).toEqual({
      filled: true,
      colorVar: '--up',
      pulsing: false,
      tooltip: null,
      countLabel: '(4)',
    })
  })

  it('shows a dimmed (not bright green) dot when enabled but the count is zero', () => {
    expect(describeSourceIndicator({ enabled: true, count: 0 })).toEqual({
      filled: true,
      colorVar: '--text-dim',
      pulsing: false,
      tooltip: null,
      countLabel: '(0)',
    })
  })

  it('shows a hollow red dot, still with its count label, when disabled and settled', () => {
    // The count reflects what's already loaded regardless of on/off state,
    // so a disabled source's count stays visible (e.g. to decide whether
    // it's worth turning on).
    expect(describeSourceIndicator({ enabled: false, count: 4 })).toEqual({
      filled: false,
      colorVar: '--down',
      pulsing: false,
      tooltip: null,
      countLabel: '(4)',
    })
  })

  it('shows a filled, pulsing, dim dot while loading, regardless of count', () => {
    expect(describeSourceIndicator({ enabled: true, count: 7, loading: true })).toEqual({
      filled: true,
      colorVar: '--text-dim',
      pulsing: true,
      tooltip: null,
      countLabel: null,
    })
  })

  it('shows a filled red (non-pulsing) dot with a tooltip on error', () => {
    expect(describeSourceIndicator({ enabled: true, count: null, error: 'Network error' })).toEqual({
      filled: true,
      colorVar: '--down',
      pulsing: false,
      tooltip: 'Network error',
      countLabel: null,
    })
  })

  it('error takes precedence over loading if both are somehow set', () => {
    const result = describeSourceIndicator({ enabled: true, count: 2, loading: true, error: 'boom' })
    expect(result.tooltip).toBe('boom')
    expect(result.pulsing).toBe(false)
  })

  it('returns no count label when the count is not yet known', () => {
    expect(describeSourceIndicator({ enabled: false, count: null }).countLabel).toBeNull()
    expect(describeSourceIndicator({ enabled: true, count: null }).countLabel).toBeNull()
  })
})
