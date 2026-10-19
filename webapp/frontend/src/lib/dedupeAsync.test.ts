import { describe, expect, it, vi } from 'vitest'
import { dedupeAsync } from './dedupeAsync'

describe('dedupeAsync', () => {
  it('collapses two concurrent calls for the same key into one underlying call', async () => {
    const inFlight = new Map<string, Promise<number>>()
    let calls = 0
    const fn = () => {
      calls += 1
      return Promise.resolve(42)
    }

    const [a, b] = await Promise.all([
      dedupeAsync('AAPL', inFlight, fn),
      dedupeAsync('AAPL', inFlight, fn),
    ])

    expect(calls).toBe(1)
    expect(a).toBe(42)
    expect(b).toBe(42)
  })

  it('runs different keys independently', async () => {
    const inFlight = new Map<string, Promise<number>>()
    let calls = 0
    const fn = () => {
      calls += 1
      return Promise.resolve(calls)
    }

    const [a, b] = await Promise.all([
      dedupeAsync('AAPL', inFlight, fn),
      dedupeAsync('MSFT', inFlight, fn),
    ])

    expect(calls).toBe(2)
    expect(a).not.toBe(b)
  })

  it('allows a fresh call for the same key once the previous one has resolved', async () => {
    const inFlight = new Map<string, Promise<number>>()
    let calls = 0
    const fn = vi.fn(() => {
      calls += 1
      return Promise.resolve(calls)
    })

    const first = await dedupeAsync('AAPL', inFlight, fn)
    const second = await dedupeAsync('AAPL', inFlight, fn)

    expect(fn).toHaveBeenCalledTimes(2)
    expect(first).toBe(1)
    expect(second).toBe(2)
  })
})
