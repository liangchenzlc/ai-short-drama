import { getAttemptAccount } from '../generations/attempt';

export type RecoveryStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
export class CreationRecoveryError extends Error {}

export function creationScope(scope: string): string {
  const account = getAttemptAccount() ?? 'anonymous';
  return `creation-recovery:user:${account}:${scope}`;
}
export function recoveryStorage(): RecoveryStorage | null {
  try { return window.localStorage; } catch { return null; }
}
export function readRecoveryText(scope: string, storage = recoveryStorage()): string | null {
  try { return storage?.getItem(scope) ?? null; }
  catch { throw new CreationRecoveryError('无法读取本机恢复记录，请检查浏览器存储权限。'); }
}
export function readRecovery<T>(scope: string, validate: (value: unknown) => value is T, storage = recoveryStorage()): T | null {
  try {
    const raw = storage?.getItem(scope);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (validate(value)) return value;
  } catch { throw new CreationRecoveryError('无法读取本机恢复记录，请保留当前内容并检查浏览器存储。'); }
  throw new CreationRecoveryError('本机恢复记录损坏，已阻止新请求。请先下载草稿并核对任务记录。');
}
export function saveRecovery(scope: string, value: unknown, storage = recoveryStorage()): void {
  try {
    if (!storage) throw new Error();
    storage.setItem(scope, JSON.stringify(value));
  } catch { throw new CreationRecoveryError('无法保存本机恢复记录，请检查浏览器存储空间与权限后重试。'); }
}
export function clearRecovery(scope: string, storage = recoveryStorage()): void {
  try { storage?.removeItem(scope); } catch { throw new CreationRecoveryError('无法清除本机恢复记录，请核对已保存内容后重试。'); }
}

export interface StoredDraft<T> { format: 1; base_version: string; document: T }
export function isStoredDraft(value: unknown): value is StoredDraft<unknown> {
  if (!value || typeof value !== 'object') return false;
  const draft = value as Partial<StoredDraft<unknown>>;
  return draft.format === 1 && typeof draft.base_version === 'string' && /^[0-9]+$/.test(draft.base_version) && 'document' in draft;
}
