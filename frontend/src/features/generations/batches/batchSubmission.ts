import { attemptStorage, getAttemptAccount } from '@host/features/shared/async/request-receipt';
import type { BatchRequest, BatchScene, BatchScope } from '@host/types/generations/batches/requests';

export type BatchSubmission = {
  parts: { value: BatchRequest & { preflight_hash: string; accepted_ids: string[] }; started?: boolean; rejected?: boolean; id?: string }[];
  retryIds: string[];
  pendingRetry?: { batchId: string; itemIds: string[] };
};
export type SavedBatchSubmission = { scopeKey: string; submission: BatchSubmission };

export const batchSubmissionChanged = 'batchSubmissionChanged';

export function notifyBatchSubmissionChanged() {
  window.dispatchEvent(new Event(batchSubmissionChanged));
}

function submissionSlot(scope: string) {
  const storage = attemptStorage();
  const account = getAttemptAccount() ?? storage?.getItem('short-drama:account') ?? '';
  return `batch-submission:${account}:${scope}`;
}

export function readSubmission(scope: string): BatchSubmission | null {
  try {
    const value = attemptStorage()?.getItem(submissionSlot(scope));
    return value ? JSON.parse(value) as BatchSubmission : null;
  } catch {
    return null;
  }
}

export function writeSubmission(scope: string, submission: BatchSubmission | null) {
  try {
    const storage = attemptStorage();
    const slot = submissionSlot(scope);
    if (submission) storage?.setItem(slot, JSON.stringify(submission));
    else storage?.removeItem(slot);
  } catch {
    // The current panel still retains the submission in memory.
  }
  notifyBatchSubmissionChanged();
}

export function readProjectBatchSubmissions(projectId: string | undefined): SavedBatchSubmission[] {
  const storage = attemptStorage();
  if (!storage) return [];
  const prefix = submissionSlot('batch:');
  const saved: SavedBatchSubmission[] = [];
  for (const slot of Object.keys(storage)) {
    if (!slot.startsWith(prefix)) continue;
    const scopeKey = slot.slice(submissionSlot('').length);
    const submission = readSubmission(scopeKey);
    const value = submission?.parts[0]?.value;
    if (submission && value?.scope.library === 'project' && value.scope.project_id === projectId) saved.push({ scopeKey, submission });
  }
  return saved;
}

export function findSubmission(scope: BatchScope, scene: BatchScene): SavedBatchSubmission | null {
  const saved = readProjectBatchSubmissions(scope.project_id).filter(({ submission }) => submission.parts[0].value.scene === scene);
  return saved.find(({ submission }) => submission.pendingRetry || submission.parts.some((part) => !part.id))
    ?? saved.at(-1) ?? null;
}
