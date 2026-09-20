type AttemptStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const prefix = 'generation-attempt:';
// Only a digest and a random key are persisted, never prompts or credentials.
const fallback = new Map<string, string>();
export function attemptStorage(): AttemptStorage | null {
  try { return window.sessionStorage; } catch { return null; }
}
export async function requestAttempt(scope: string, payload: unknown, storage: AttemptStorage | null): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(payload)));
  const fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const slot = prefix + scope;
  let previous = fallback.get(slot);
  try { previous = storage?.getItem(slot) ?? previous; } catch { /* Browser storage may be disabled. */ }
  if (previous) {
    try { const saved = JSON.parse(previous); if (saved.fingerprint === fingerprint && typeof saved.key === 'string') return saved.key; } catch { /* Ignore corrupted storage. */ }
  }
  const key = crypto.randomUUID();
  const value = JSON.stringify({ fingerprint, key });
  fallback.set(slot, value);
  try { storage?.setItem(slot, value); } catch { /* Keep the in-memory attempt. */ }
  return key;
}
export function clearAttempt(scope: string, storage: AttemptStorage | null) {
  fallback.delete(prefix + scope);
  try { storage?.removeItem(prefix + scope); } catch { /* In-memory attempt is already cleared. */ }
}
export function isServerId(value: string): boolean {
  return /^[0-9]{1,20}$/.test(value) && BigInt(value) > 0n && BigInt(value) <= 18446744073709551615n;
}
