type AttemptStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null;
const pending = new Map<string, string | null>();
const slot = (id: string) => `shot-image-uncertain:${id}`;

export function pendingShotAttempt(id: string, storage: AttemptStorage): string | null {
  if (pending.has(id)) return pending.get(id) ?? null;
  try { return storage?.getItem(slot(id)) ?? null; } catch { return null; }
}

export function startShotAttempt(id: string, owner: string, storage: AttemptStorage) {
  pending.set(id, owner);
  try { storage?.setItem(slot(id), owner); } catch {}
}

export function finishShotAttempt(id: string, owner: string, storage: AttemptStorage): boolean {
  if (pendingShotAttempt(id, storage) !== owner) return false;
  pending.set(id, null);
  try { storage?.removeItem(slot(id)); } catch {}
  return true;
}
