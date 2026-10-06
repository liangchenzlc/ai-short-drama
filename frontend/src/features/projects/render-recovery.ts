import type { RenderBody } from '../../api/modules/assembly';
import { CreationRecoveryError, readRecoveryText, saveRecovery, clearRecovery, type RecoveryStorage } from './creation-recovery';

export interface RenderAttempt {
  format: 1; kind: 'export' | 'preview' | 'retry'; key: string;
  body: RenderBody | Record<string, never>; job_id?: string;
}
const rejectedBeforeCreation = new Set([
  'assembly_version_conflict', 'assembly_source_changed', 'assembly_not_ready',
  'assembly_stale_source', 'assembly_limit', 'assembly_job_state', 'sound_review_required',
  'subtitle_font_missing', 'audio_dialogue_stale', 'audio_timing', 'subtitle_timing',
]);
export function definiteRenderRejection(code: string, status?: number): boolean {
  return !!status && status >= 400 && status < 500 && rejectedBeforeCreation.has(code);
}
function validAttempt(value: unknown): value is RenderAttempt {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<RenderAttempt>;
  if (record.format !== 1 || !['export', 'preview', 'retry'].includes(record.kind ?? '') || typeof record.key !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(record.key) || !record.body || typeof record.body !== 'object') return false;
  if (record.kind === 'retry') return typeof record.job_id === 'string' && /^[0-9]+$/.test(record.job_id) && Object.keys(record.body).length === 0;
  const body = record.body as Partial<RenderBody>;
  return typeof body.row_version === 'string' && /^[0-9]+$/.test(body.row_version) && typeof body.source_hash === 'string' && /^[a-f0-9]{64}$/.test(body.source_hash) && typeof body.acknowledge_stale_source === 'boolean';
}
export function readRenderRecoveryRecord(scope: string, storage?: RecoveryStorage | null): { attempt: RenderAttempt | null; damaged: string | null } {
  const raw = readRecoveryText(scope, storage);
  if (raw === null) return { attempt: null, damaged: null };
  try {
    const value: unknown = JSON.parse(raw);
    if (validAttempt(value)) return { attempt: value, damaged: null };
  } catch { /* Keep the original record available for download and explicit review. */ }
  return { attempt: null, damaged: raw };
}
export function readRenderAttempt(scope: string, storage?: RecoveryStorage | null) {
  const record = readRenderRecoveryRecord(scope, storage);
  if (record.damaged !== null) throw new CreationRecoveryError('合成请求恢复记录无法读取，已阻止新请求。请先下载完整记录并核对任务中心、导出和预览记录。');
  return record.attempt;
}
export function discardDamagedRenderAttempt(scope: string, expectedRaw: string, storage?: RecoveryStorage | null): void {
  const record = readRenderRecoveryRecord(scope, storage);
  if (record.attempt) throw new CreationRecoveryError('当前合成请求仍可恢复，请使用原参数和原请求编号核对受理结果。');
  if (record.damaged === null || record.damaged !== expectedRaw) throw new CreationRecoveryError('本机合成请求恢复记录已变化，请重新读取并核对后再清除。');
  clearRecovery(scope, storage);
}
export function beginRenderAttempt(scope: string, operation: Omit<RenderAttempt, 'format' | 'key'>, storage?: RecoveryStorage | null): RenderAttempt {
  const existing = readRenderAttempt(scope, storage);
  if (existing) {
    if (existing.kind !== operation.kind || existing.job_id !== operation.job_id || JSON.stringify(existing.body) !== JSON.stringify(operation.body)) throw new CreationRecoveryError('上次合成请求尚未核对，请先核对原请求，避免重复制作。');
    return existing;
  }
  const attempt: RenderAttempt = { ...operation, format: 1, key: crypto.randomUUID() };
  saveRecovery(scope, attempt, storage);
  return attempt;
}
export function finishRenderAttempt(scope: string, attempt: RenderAttempt, storage?: RecoveryStorage | null) {
  if (readRenderAttempt(scope, storage)?.key === attempt.key) clearRecovery(scope, storage);
}
