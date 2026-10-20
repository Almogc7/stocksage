import type { Candle, VolumeBar } from './api'

/** Monday (UTC) of the calendar week containing dateStr, as 'YYYY-MM-DD'. */
export function weekStartKey(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00Z`)
  const dayOfWeek = d.getUTCDay() // 0 = Sun .. 6 = Sat
  const offsetFromMonday = (dayOfWeek + 6) % 7
  d.setUTCDate(d.getUTCDate() - offsetFromMonday)
  return d.toISOString().slice(0, 10)
}

/** Groups consecutive daily bars (ascending, as the API returns them) into
 * weekly OHLCV bars keyed by weekStartKey. open = first day's open in the
 * week, high/low = max/min, close = last day's close, volume = sum. The
 * last group is implicitly partial - it just has fewer days baked in - by
 * construction, with no extra flag needed. */
export function aggregateWeekly(
  candles: Candle[],
  volume: VolumeBar[],
): { candles: Candle[]; volume: VolumeBar[] } {
  const volByTime = new Map(volume.map((v) => [v.time, v.value]))
  const weekCandles: Candle[] = []
  const weekVolumes: VolumeBar[] = []
  let currentKey: string | null = null

  for (const c of candles) {
    const key = weekStartKey(c.time)
    const vol = volByTime.get(c.time) ?? 0
    if (key !== currentKey) {
      currentKey = key
      weekCandles.push({ time: key, open: c.open, high: c.high, low: c.low, close: c.close })
      weekVolumes.push({ time: key, value: vol })
    } else {
      const bar = weekCandles[weekCandles.length - 1]
      bar.high = Math.max(bar.high, c.high)
      bar.low = Math.min(bar.low, c.low)
      bar.close = c.close
      weekVolumes[weekVolumes.length - 1].value += vol
    }
  }

  return { candles: weekCandles, volume: weekVolumes }
}
