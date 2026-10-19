/** Runs `fn` for `key`, but if a call for that key is already in flight,
 * returns the same promise instead of starting a second one. Used to
 * collapse accidental duplicate triggers (e.g. a side effect that fires
 * twice before the first call resolves) into a single underlying call. */
export function dedupeAsync<T>(
  key: string,
  inFlight: Map<string, Promise<T>>,
  fn: () => Promise<T>,
): Promise<T> {
  const existing = inFlight.get(key)
  if (existing) return existing
  const promise = fn().finally(() => inFlight.delete(key))
  inFlight.set(key, promise)
  return promise
}
