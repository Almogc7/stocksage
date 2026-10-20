export interface TimedValue {
  time: string
  value: number
}

/** Simple moving average over a full series, keyed by the same `time`
 * strings as the source candles. Points before the window has `period`
 * bars of lead-in are omitted (no partial-window SMA). */
export function sma(
  points: { time: string; close: number }[],
  period: number,
): TimedValue[] {
  const out: TimedValue[] = []
  let sum = 0
  for (let i = 0; i < points.length; i++) {
    sum += points[i].close
    if (i >= period) sum -= points[i - period].close
    if (i >= period - 1) {
      out.push({ time: points[i].time, value: sum / period })
    }
  }
  return out
}
