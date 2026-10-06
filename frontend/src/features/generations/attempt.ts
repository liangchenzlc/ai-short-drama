type AttemptStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const prefix = 'generation-attempt:';
// Only a digest and a random key are persisted, never prompts or credentials.
const fallback = new Map<string, string>();
let currentAccount: string | null | undefined;
export function setAttemptAccount(accountId: string | null) {
  currentAccount = accountId;
  try {
    if (accountId) window.sessionStorage.setItem('short-drama:account', accountId);
    else window.sessionStorage.removeItem('short-drama:account');
  } catch { /* Account isolation also works when browser storage is unavailable. */ }
}
export function getAttemptAccount(): string | null {
  return currentAccount ?? null;
}
function attemptSlot(scope: string) {
  let account = currentAccount ?? '';
  if (currentAccount === undefined) {
    try { if (typeof window !== 'undefined') account = window.sessionStorage.getItem('short-drama:account') ?? ''; } catch { /* In-memory recovery remains available. */ }
  }
  return prefix + (account ? `user:${account}:` : '') + scope;
}
export function attemptStorage(): AttemptStorage | null {
  try { return window.sessionStorage; } catch { return null; }
}
export async function requestAttempt(scope: string, payload: unknown, storage: AttemptStorage | null): Promise<string> {
  const slot = attemptSlot(scope);
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(payload)));
  const fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
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
  const slot = attemptSlot(scope);
  fallback.delete(slot);
  try { storage?.removeItem(slot); } catch { /* In-memory attempt is already cleared. */ }
}
export function isServerId(value: string): boolean {
  return /^[0-9]{1,20}$/.test(value) && BigInt(value) > 0n && BigInt(value) <= 18446744073709551615n;
}
