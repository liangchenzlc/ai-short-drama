import { agentArtifactsApi } from '@host/api/assistant/operations/artifacts';
import { assetExtractionApi } from '@host/api/creation/assets/extraction';
import { generations } from '@host/api/generations/tasks/requests';
import { errorMessage } from '@host/api/http';
import { savedConfigId } from '@host/features/ai-config/selection/config-selection';
import { artifactVersion } from '@host/features/assistant/operations/agent-artifact-presentation';
import {
  defaultAdoption,
  extractionApplyRequest,
  extractionDraftError,
  ExtractionReviewError,
  missingExtractionTaskIds,
  scriptAssetsRequest,
} from '@host/features/creation/assets/extraction/contract';
import { ExtractionCandidateCard } from '@host/features/creation/assets/extraction/ExtractionCandidateCard';
import { ExtractionHistory } from '@host/features/creation/assets/extraction/ExtractionHistory';
import { ExtractionSettings } from '@host/features/creation/assets/extraction/ExtractionSettings';
import { allKinds, labels } from '@host/features/creation/assets/extraction/presentation';
import {
  clearRecovery,
  creationScope,
  readRecovery,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { releaseStandardSession, retainStandardSession } from '@host/features/creation/shared/sessions/registry';
import { ResourceEditingSession } from '@host/features/creation/shared/sessions/resource-editing-session';
import type { WritingSession } from '@host/features/creation/writing/sessions/writing-session';
import { generationError, taskLabel } from '@host/features/generations/presentation/presentation';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { AgentArtifactDetail } from '@host/types/assistant/artifacts';
import type { ExtractionCandidate, ExtractionResult } from '@host/types/creation/assets/extraction';
import type { ExtractionDrafts } from '@host/types/creation/assets/extraction/ScriptAssetExtraction';
import type { AssetDraft, AssetKind } from '@host/types/creation/assets/management';
import type { ResourceRecovery } from '@host/types/creation/shared/sessions/resource-editing-session';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { GenerationSummary } from '@host/types/generations/api';
import { Alert, Button, Checkbox, Empty, Spin, Tabs } from 'antd';
import { History, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ComponentProps } from 'react';
const active = (task: GenerationSummary) => task.status === 'queued' || task.status === 'running';
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const noReviewSubscribe = () => () => {};
const noReviewSnapshot = () => null;
function validExtractionRecovery(value: unknown): value is ResourceRecovery<ExtractionDrafts> {
  if (!value || typeof value !== 'object') return false;
  const item = value as ResourceRecovery<ExtractionDrafts>;
  return (
    item.format === 1 &&
    typeof item.base_version === 'string' &&
    !!item.document &&
    Object.values(item.document).every(
      (draft) =>
        draft &&
        allKinds.includes(draft.kind) &&
        typeof draft.name === 'string' &&
        typeof draft.description === 'string' &&
        typeof draft.prompt === 'string' &&
        Array.isArray(draft.tags),
    )
  );
}

export function ScriptAssetExtraction({
  projectId,
  session,
  readOnly,
  modelId,
  onModelChange,
  onApplied,
  onConfirmScript,
  registerBarrier,
  externalReview,
  embedded = false,
  presentation = 'default',
}: {
  projectId: string;
  session: WritingSession;
  readOnly: boolean;
  modelId: string;
  onModelChange: (id: string) => void;
  onApplied: () => void;
  onConfirmScript: () => void;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
  externalReview?: { artifact: AgentArtifactDetail; nonce: number } | null;
  embedded?: boolean;
  presentation?: 'default' | 'standard-creation';
}) {
  const auth = useAuth();
  const alive = useRef(true);
  const currentScope = useRef({
    key: '',
    readOnly,
    artifactId: externalReview?.artifact.id,
    nonce: externalReview?.nonce,
  });
  currentScope.current = {
    key: `${projectId}:${auth.user?.id ?? 'anonymous'}`,
    readOnly,
    artifactId: externalReview?.artifact.id,
    nonce: externalReview?.nonce,
  };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const writing = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const api = useMemo(() => assetExtractionApi(projectId), [projectId]);
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<'settings' | 'result' | 'history'>('settings');
  const [kinds, setKinds] = useState<AssetKind[]>(allKinds);
  const [instructions, setInstructions] = useState('');
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [activeId, setActiveId] = useState<string | null>(null);
  const validationPaused = useRef(false);
  const accountId = auth.user?.id ?? 'anonymous';
  const reviewKey = `asset-extraction:${projectId}:${activeId}`;
  const reviewSession = useMemo(() => {
    if (!activeId) return null;
    const id = activeId;
    const scope = creationScope(reviewKey, accountId);
    return retainStandardSession(
      accountId,
      reviewKey,
      () =>
        new ResourceEditingSession<ExtractionResult, ExtractionDrafts>({
          read: () => api.get(id),
          save: (resource, document) => {
            const invalidDraft = Object.values(document).map(extractionDraftError).find(Boolean);
            if (invalidDraft) {
              validationPaused.current = true;
              throw Object.assign(new ExtractionReviewError(invalidDraft), {
                status: 422,
              });
            }
            validationPaused.current = false;
            return api.save(id, {
              result_version: resource.result_version,
              items: resource.items
                .filter(
                  (item) =>
                    !item.applied && document[item.candidate_id] && !same(item.draft, document[item.candidate_id]),
                )
                .map((item) => ({
                  candidate_id: item.candidate_id,
                  draft: document[item.candidate_id],
                })),
            });
          },
          version: (resource) => resource.result_version,
          document: (resource) =>
            Object.fromEntries(
              resource.items.filter((item) => !item.applied).map((item) => [item.candidate_id, item.draft]),
            ),
          apply: (resource, document) => ({
            ...resource,
            items: resource.items.map((item) => ({
              ...item,
              draft: document[item.candidate_id] ?? item.draft,
            })),
          }),
          recovery: {
            read: () => readRecovery(scope, validExtractionRecovery),
            save: (value) => saveRecovery(scope, value),
            clear: () => clearRecovery(scope),
            export: () => readRecoveryText(scope),
            backup: (raw) => saveRecovery(`${scope}:previous`, { raw }),
            previous: () => readRecoveryText(`${scope}:previous`),
          },
        }),
      { label: '素材提取结果', projectId },
    );
  }, [accountId, api, activeId, reviewKey, projectId]);
  const review = useSyncExternalStore(
    reviewSession?.subscribe ?? noReviewSubscribe,
    reviewSession?.getSnapshot ?? noReviewSnapshot,
  );
  useEffect(
    () => () => {
      if (reviewSession) releaseStandardSession(accountId, reviewKey);
    },
    [reviewSession, accountId, reviewKey],
  );
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [drafts, setDrafts] = useState<Record<string, AssetDraft>>({});
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<AssetKind>('character');
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyOffset, setHistoryOffset] = useState(0);
  const historySequence = useRef(0);
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;
  const actionLock = useRef(false);
  useEffect(() => {
    const generationId = externalReview?.artifact.generation_task_id;
    if (!generationId) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    generations
      .detail(generationId, controller.signal)
      .then((task) => {
        if (controller.signal.aborted) return;
        setTasks((old) => [task, ...old.filter((item) => item.generation_id !== generationId)]);
        setActiveId(generationId);
        setResult(null);
        setDrafts({});
        setEditing(null);
        setPhase('result');
        setOpen(true);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(cause));
          setOpen(true);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [externalReview?.nonce]);
  const task = tasks.find((item) => item.generation_id === activeId);
  const dirty =
    review?.dirty ??
    !!result?.items.some(
      (item) => !item.applied && drafts[item.candidate_id] && !same(item.draft, drafts[item.candidate_id]),
    );
  const invalid = !!result?.items.some(
    (item) => !item.applied && !!extractionDraftError(drafts[item.candidate_id] ?? item.draft),
  );
  const canExtract = writing.loaded && !!writing.script.trim() && !readOnly;
  const stale = !!result?.stale;
  const pending = tasks.some(active);

  const refreshTasks = useCallback(
    async (signal?: AbortSignal) => {
      const requestNo = ++historySequence.current;
      const expectedScope = currentScope.current.key;
      setHistoryLoading(true);
      try {
        const page = await generations.list(
          {
            service_type: 'text',
            project_id: projectId,
            source_scene: 'script_assets',
            offset: 0,
            limit: 20,
          },
          signal,
        );
        const missing = missingExtractionTaskIds(tasksRef.current, page.items);
        const details = await Promise.allSettled(missing.map((id) => generations.detail(id, signal)));
        if (
          signal?.aborted ||
          !alive.current ||
          requestNo !== historySequence.current ||
          currentScope.current.key !== expectedScope
        )
          return;
        const resolved = details.flatMap((detail) =>
          detail.status === 'fulfilled' &&
          detail.value.source?.scene === 'script_assets' &&
          detail.value.source.project_id === projectId
            ? [detail.value]
            : [],
        );
        const incoming = [...page.items, ...resolved];
        setTasks((previous) => [
          ...incoming,
          ...previous.filter((item) => !incoming.some((next) => next.generation_id === item.generation_id)),
        ]);
        setTotal(page.total);
        setHistoryOffset((previous) => Math.max(previous, page.items.length));
        setHistoryError(
          resolved.length < missing.length ? '部分活动提取任务暂时无法核对，已保留上次状态。请重试查询。' : '',
        );
        setActiveId((current) => current ?? page.items[0]?.generation_id ?? null);
      } catch (cause) {
        if (
          !signal?.aborted &&
          alive.current &&
          requestNo === historySequence.current &&
          currentScope.current.key === expectedScope
        )
          setHistoryError(errorMessage(cause));
      } finally {
        if (
          !signal?.aborted &&
          alive.current &&
          requestNo === historySequence.current &&
          currentScope.current.key === expectedScope
        )
          setHistoryLoading(false);
      }
    },
    [projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refreshTasks(controller.signal);
    const timer =
      open || pending
        ? window.setInterval(() => {
            void refreshTasks(controller.signal);
          }, 4000)
        : undefined;
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refreshTasks, open, pending]);

  function acceptResult(next: ExtractionResult, reset: boolean) {
    reviewSession?.initialize(next);
    const current = reviewSession?.getSnapshot().value ?? next;
    setResult(current);
    setDrafts(Object.fromEntries(current.items.map((item) => [item.candidate_id, item.draft])));
    setChoices((previous) =>
      Object.fromEntries(
        next.items.map((item) => {
          const old = previous[item.candidate_id];
          const before = result?.items.find((prior) => prior.candidate_id === item.candidate_id);
          const changedMatches = !!before && !same(before.matches, item.matches);
          const choice =
            !reset && !changedMatches && (old === 'create' || item.matches.some((match) => match.asset_id === old))
              ? old
              : defaultAdoption(item);
          return [item.candidate_id, changedMatches && item.matches.length ? '' : choice];
        }),
      ),
    );
    setSelected(
      (previous) =>
        new Set(
          next.items
            .filter((item) => !item.applied && (reset ? !!defaultAdoption(item) : previous.has(item.candidate_id)))
            .map((item) => item.candidate_id),
        ),
    );
    if (reset) {
      setTab(next.kinds[0] ?? 'character');
      setEditing(null);
    }
  }

  useEffect(() => {
    if (!review?.value) return;
    setResult(review.value);
    setDrafts(Object.fromEntries(review.value.items.map((item) => [item.candidate_id, item.draft])));
  }, [review?.value]);

  useEffect(() => {
    if (!open || phase !== 'result' || !activeId || task?.status !== 'succeeded' || dirty) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    api
      .get(activeId, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) {
          acceptResult(next, result?.generation_id !== activeId);
          setLoading(false);
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, open, phase, activeId, task?.status]);

  async function saveDrafts(): Promise<ExtractionResult | null> {
    if (!result || !dirty) return result;
    if (readOnly || invalid) throw new ExtractionReviewError('名称、描述和图片生成提示词均不能为空，请补充后保存。');
    if (reviewSession) {
      if (!(await reviewSession.flush()))
        throw new ExtractionReviewError(reviewSession.getSnapshot().message || '提取结果尚未同步，修改已保留。');
      return reviewSession.getSnapshot().value;
    }
    const next = await api.save(result.generation_id, {
      result_version: result.result_version,
      items: result.items
        .filter((item) => !item.applied && !same(item.draft, drafts[item.candidate_id]))
        .map((item) => ({
          candidate_id: item.candidate_id,
          draft: drafts[item.candidate_id],
        })),
    });
    acceptResult(next, false);
    return next;
  }

  async function run(action: () => Promise<void>) {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof ExtractionReviewError ? cause.message : errorMessage(cause));
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  }

  const barrierRef = useRef<NavigationBarrier>({
    hasUnsettled: () => false,
    flush: async () => true,
  });
  barrierRef.current = {
    hasUnsettled: () => dirty || actionLock.current,
    flush: async () => {
      if (actionLock.current) return false;
      let saved = false;
      await run(async () => {
        await saveDrafts();
        saved = true;
      });
      return saved;
    },
  };
  useEffect(() => {
    registerBarrier({
      hasUnsettled: () => barrierRef.current.hasUnsettled(),
      flush: () => barrierRef.current.flush(),
    });
    return () => registerBarrier(null);
  }, [registerBarrier]);

  const close = () => {
    setOpen(false);
    if (embedded) setPhase('settings');
    void reviewSession?.flush();
  };
  async function generate() {
    if (!canExtract || !kinds.length || pending) return;
    await run(async () => {
      await saveDrafts();
      if (!(await session.flush())) throw new ExtractionReviewError('请先完成正文保存，再提取素材。');
      const latest = session.getSnapshot();
      if (!latest.scriptId || !latest.script.trim())
        throw new ExtractionReviewError('先编写或采用一份剧本，再提取素材。');
      const configId = savedConfigId(modelId);
      const body = {
        ...scriptAssetsRequest(projectId, latest.scriptId, latest.contentVersion, kinds, instructions),
        ...(configId ? { config_id: configId } : {}),
      };
      const scope = `script-assets:${projectId}`;
      const key = await requestAttempt(scope, body, attemptStorage());
      try {
        const receipt = await generations.generateText(body, key);
        clearAttempt(scope, attemptStorage());
        setActiveId(receipt.generation_id);
        setResult(null);
        setDrafts({});
        setPhase('result');
        setOpen(true);
        await refreshTasks();
      } catch (cause) {
        throw new ExtractionReviewError(generationError(cause));
      }
    });
  }

  async function adopt() {
    if (readOnly || stale) return;
    const intent = currentScope.current;
    const artifact = externalReview?.artifact;
    const checkAgentScope = () => {
      if (
        artifact &&
        (!alive.current ||
          currentScope.current.key !== intent.key ||
          currentScope.current.readOnly ||
          currentScope.current.artifactId !== intent.artifactId ||
          currentScope.current.nonce !== intent.nonce)
      )
        throw new ExtractionReviewError('当前创作范围已变化，本次未采用。请重新核对素材候选。');
    };
    await run(async () => {
      checkAgentScope();
      const saved = await saveDrafts();
      if (!saved) return;
      if (
        result &&
        !same(
          result.items.map((item) => item.matches),
          saved.items.map((item) => item.matches),
        )
      ) {
        throw new ExtractionReviewError('候选已保存，匹配建议有变化，请核对采用方式后再次加入。');
      }
      if (!(await session.flush())) throw new ExtractionReviewError('请先完成正文保存。');
      const latest = session.getSnapshot();
      const body = extractionApplyRequest(saved, selected, choices, latest.contentVersion);
      const scope = `script-assets-apply:${saved.generation_id}`;
      const key = await requestAttempt(scope, body, attemptStorage());
      checkAgentScope();
      const receipt =
        artifact?.generation_task_id === saved.generation_id
          ? ((
              await agentArtifactsApi(projectId).adopt(artifact.id, {
                row_version: artifactVersion(artifact.row_version),
                content_version: artifactVersion(latest.contentVersion),
                native_review: { ...body },
              })
            ).apply_receipt as {
              created: number;
              reused: number;
              already_applied: boolean;
            })
          : await api.apply(saved.generation_id, body, key);
      if (
        artifact &&
        (!alive.current ||
          currentScope.current.key !== intent.key ||
          currentScope.current.artifactId !== intent.artifactId ||
          currentScope.current.nonce !== intent.nonce)
      )
        return;
      clearAttempt(scope, attemptStorage());
      onApplied();
      if (artifact?.generation_task_id === saved.generation_id)
        window.dispatchEvent(new Event('agent-artifacts-updated'));
      setMessage(
        receipt.already_applied
          ? '这批素材已加入本集素材库。'
          : `已加入本集素材库：新增 ${receipt.created} 项，复用 ${receipt.reused} 项。`,
      );
      acceptResult(await api.get(saved.generation_id), false);
      setEditing(null);
    });
  }

  async function reloadResult() {
    await run(async () => {
      if (reviewSession) {
        const next = await reviewSession.reload();
        if (next) acceptResult(next, true);
      } else if (activeId) acceptResult(await api.get(activeId), true);
    });
  }

  async function showTask(id: string) {
    await run(async () => {
      void reviewSession?.flush();
      setActiveId(id);
      setResult(null);
      setDrafts({});
      setEditing(null);
      setPhase('result');
    });
  }

  async function moreHistory() {
    await run(async () => {
      const expectedScope = currentScope.current.key;
      const page = await generations.list({
        service_type: 'text',
        project_id: projectId,
        source_scene: 'script_assets',
        offset: historyOffset,
        limit: 20,
      });
      if (!alive.current || currentScope.current.key !== expectedScope) return;
      setTasks((previous) => [
        ...previous,
        ...page.items.filter((item) => !previous.some((old) => old.generation_id === item.generation_id)),
      ]);
      setTotal(page.total);
      setHistoryOffset((previous) => previous + page.items.length);
    });
  }

  function editDraft(item: ExtractionCandidate, patch: Partial<AssetDraft>) {
    const next = {
      ...drafts,
      [item.candidate_id]: {
        ...(drafts[item.candidate_id] ?? item.draft),
        ...patch,
      },
    };
    setDrafts(next);
    reviewSession?.edit(next);
    if (validationPaused.current && !Object.values(next).some(extractionDraftError)) {
      validationPaused.current = false;
      void reviewSession?.retry();
    }
    if (patch.name !== undefined || patch.kind !== undefined || patch.scene_time !== undefined)
      setChoices((previous) => ({ ...previous, [item.candidate_id]: '' }));
  }

  function downloadRecovery() {
    try {
      const raw = reviewSession?.exportRecovered();
      if (!raw) return;
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `asset-extraction-${activeId}-recovery.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  const rows = result?.items.filter((item) => (drafts[item.candidate_id] ?? item.draft).kind === tab) ?? [];
  const selectable = rows.filter((item) => !item.applied);
  const chosen = result?.items.filter((item) => !item.applied && selected.has(item.candidate_id)) ?? [];
  const selectItem = (id: string, checked: boolean) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  const Frame = embedded && phase === 'settings' ? ExtractionInlineFrame : Dialog;

  return (
    <>
      {!embedded && (
        <Button
          type={presentation === 'standard-creation' ? 'text' : undefined}
          aria-label={presentation === 'standard-creation' ? '资产提取记录' : undefined}
          title={presentation === 'standard-creation' ? '资产提取记录' : undefined}
          icon={
            presentation === 'standard-creation' ? (
              <History size={16} aria-hidden="true" />
            ) : (
              <Sparkles size={17} aria-hidden="true" />
            )
          }
          loading={presentation === 'standard-creation' && busy}
          onClick={() => {
            setOpen(true);
            setPhase(presentation === 'standard-creation' ? 'history' : activeId ? 'result' : 'settings');
          }}
        >
          {presentation === 'standard-creation' ? null : '从剧本提取素材'}
        </Button>
      )}
      {(open || (embedded && phase === 'settings')) && (
        <Frame
          title={phase === 'settings' ? '从剧本提取素材' : phase === 'history' ? '素材提取记录' : '素材提取结果'}
          className={`asset-extraction-dialog standard-creation-dialog${phase === 'settings' ? ' is-settings' : ''}`}
          canClose={!busy}
          onClose={close}
        >
          <div className="extraction-body">
            {error && (
              <StudioFeedback
                type="error"
                title="素材提取"
                noticeKey={`asset-extraction:${projectId}`}
                message={error}
                actions={
                  phase === 'result' && task?.status === 'succeeded' ? (
                    <Button disabled={busy} onClick={() => void reloadResult()}>
                      重新载入结果
                    </Button>
                  ) : undefined
                }
              />
            )}
            {phase === 'result' && review && ['error', 'conflict'].includes(review.status) && (
              <StudioFeedback
                type={review.status === 'conflict' ? 'warning' : 'error'}
                title="素材提取结果保存"
                noticeKey={`asset-extraction-review:${activeId}`}
                message={review.message}
                actions={
                  <>
                    {review.status === 'error' && (
                      <Button
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await reviewSession?.retry();
                          })
                        }
                      >
                        重试同步
                      </Button>
                    )}
                    {review.status === 'conflict' && (
                      <Button
                        disabled={busy || readOnly}
                        onClick={() =>
                          void run(async () => {
                            await reviewSession?.reload(true);
                          })
                        }
                      >
                        以当前版本保留本机修改
                      </Button>
                    )}
                    <Button disabled={busy} onClick={() => void reloadResult()}>
                      使用服务端版本
                    </Button>
                  </>
                }
              />
            )}
            {phase === 'result' && reviewSession?.hasPrevious() && (
              <Button type="text" onClick={downloadRecovery}>
                下载本机备份
              </Button>
            )}
            {message && <StudioFeedback type="success" title="素材提取" message={message} />}
            {phase === 'settings' && (
              <ExtractionSettings
                projectId={projectId}
                readOnly={readOnly}
                loaded={writing.loaded}
                busy={busy}
                canExtract={canExtract}
                kinds={kinds}
                setKinds={setKinds}
                instructions={instructions}
                setInstructions={setInstructions}
                modelId={modelId}
                onModelChange={onModelChange}
                pending={pending}
                onHistory={() => setPhase('history')}
                onConfirmScript={() => {
                  setOpen(false);
                  onConfirmScript();
                }}
              />
            )}
            {phase === 'history' && (
              <ExtractionHistory
                historyError={historyError}
                historyLoading={historyLoading}
                tasks={tasks}
                busy={busy}
                historyOffset={historyOffset}
                total={total}
                refreshTasks={refreshTasks}
                showTask={showTask}
                moreHistory={moreHistory}
              />
            )}
            {phase === 'result' && (
              <>
                {loading && (
                  <div className="extraction-wait" role="status">
                    <Spin />
                    <p>正在载入提取结果…</p>
                  </div>
                )}
                {!loading && (!task || active(task)) && (
                  <div className="extraction-wait" role="status">
                    <Spin />
                    <h3>{task ? taskLabel(task) : '正在查询任务'}</h3>
                    <p>正在整理角色、场景与道具。可以关闭弹窗，任务会继续处理。</p>
                    {task?.can_cancel && (
                      <Button
                        disabled={busy || readOnly}
                        onClick={() =>
                          void run(async () => {
                            await generations.cancel(task.generation_id);
                            await refreshTasks();
                          })
                        }
                      >
                        取消提取任务
                      </Button>
                    )}
                    {historyError && (
                      <Alert
                        type="error"
                        message={historyError}
                        action={<Button onClick={() => void refreshTasks()}>重试查询</Button>}
                      />
                    )}
                  </div>
                )}
                {task && ['failed', 'cancelled'].includes(task.status) && (
                  <div className="extraction-wait">
                    <Alert
                      type={task.status === 'failed' ? 'error' : 'info'}
                      showIcon
                      message={task.error?.message ?? '提取任务已取消。'}
                    />
                    {task.can_resume && (
                      <Button
                        disabled={busy || readOnly}
                        onClick={() =>
                          void run(async () => {
                            await generations.resume(task.generation_id);
                            await refreshTasks();
                          })
                        }
                      >
                        恢复结果保存
                      </Button>
                    )}
                    <Button disabled={busy || readOnly || task.can_resume} onClick={() => setPhase('settings')}>
                      调整要求并重新提取
                    </Button>
                  </div>
                )}
                {!loading && result?.generation_id === activeId && (
                  <>
                    {stale && (
                      <StudioFeedback
                        type="warning"
                        quiet
                        title="素材提取来源已变化"
                        noticeKey={`asset-extraction-stale:${activeId}`}
                        message="来源剧本已变化，结果可查看；请从当前剧本重新提取。"
                        actions={
                          <Button disabled={busy || readOnly || pending} onClick={() => setPhase('settings')}>
                            从当前剧本重新提取
                          </Button>
                        }
                      />
                    )}
                    <Tabs
                      activeKey={tab}
                      onChange={(key) => setTab(key as AssetKind)}
                      items={allKinds.map((kind) => ({
                        key: kind,
                        label: `${labels[kind]} ${result.items.filter((item) => (drafts[item.candidate_id] ?? item.draft).kind === kind).length}${!result.kinds.includes(kind) ? '（未提取）' : ''}`,
                      }))}
                    />
                    {rows.length > 0 && (
                      <div className="extraction-selection">
                        <Checkbox
                          disabled={busy || readOnly || stale || !selectable.length}
                          checked={!!selectable.length && selectable.every((item) => selected.has(item.candidate_id))}
                          indeterminate={
                            selectable.some((item) => selected.has(item.candidate_id)) &&
                            !selectable.every((item) => selected.has(item.candidate_id))
                          }
                          onChange={(event) =>
                            setSelected((previous) => {
                              const next = new Set(previous);
                              for (const item of selectable) {
                                if (event.target.checked) next.add(item.candidate_id);
                                else next.delete(item.candidate_id);
                              }
                              return next;
                            })
                          }
                        >
                          本类全选
                        </Checkbox>
                        <span>名称、描述与提示词均可编辑</span>
                      </div>
                    )}
                    {!rows.length && (
                      <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description={
                          result.kinds.includes(tab)
                            ? `未提取到${labels[tab]}，可调整要求后重新提取。`
                            : `本次未选择提取${labels[tab]}。`
                        }
                      />
                    )}
                    {rows.map((item) => (
                      <ExtractionCandidateCard
                        key={item.candidate_id}
                        item={item}
                        draft={drafts[item.candidate_id] ?? item.draft}
                        disabled={busy || readOnly || !!item.applied}
                        stale={stale}
                        dirty={dirty}
                        selected={selected.has(item.candidate_id)}
                        editing={editing === item.candidate_id}
                        choice={choices[item.candidate_id] ?? ''}
                        onSelect={(checked) => selectItem(item.candidate_id, checked)}
                        onToggle={() => setEditing(editing === item.candidate_id ? null : item.candidate_id)}
                        onEdit={(change) => editDraft(item, change)}
                        onKind={setTab}
                        onChoice={(choice) => setChoices((previous) => ({ ...previous, [item.candidate_id]: choice }))}
                      />
                    ))}
                  </>
                )}
              </>
            )}
          </div>
          <footer className="extraction-footer">
            <div>
              {phase === 'result' && result ? (
                <span>
                  已选 <strong>{chosen.length}</strong> 项
                </span>
              ) : (
                <span>仅加入本集素材库</span>
              )}
              <Button
                type="link"
                disabled={busy}
                onClick={() => {
                  setOpen(true);
                  setPhase(phase === 'history' ? (activeId ? 'result' : 'settings') : 'history');
                }}
              >
                {phase === 'history' ? '返回' : '提取记录'}
              </Button>
              {phase === 'result' && !readOnly && (
                <Button type="link" disabled={busy || pending} onClick={() => setPhase('settings')}>
                  重新提取
                </Button>
              )}
            </div>
            <div>
              {(!embedded || phase !== 'settings') && (
                <Button disabled={busy} onClick={close}>
                  {phase === 'settings' ? '取消' : pending ? '后台处理' : '稍后处理'}
                </Button>
              )}
              {phase === 'settings' && (
                <Button
                  type="primary"
                  loading={busy}
                  disabled={!canExtract || !kinds.length || pending}
                  onClick={() => void generate()}
                >
                  AI 提取素材
                </Button>
              )}
              {phase === 'result' && result && (
                <Button
                  type="primary"
                  loading={busy}
                  disabled={
                    readOnly || stale || !chosen.length || invalid || chosen.some((item) => !choices[item.candidate_id])
                  }
                  onClick={() => void adopt()}
                >
                  加入本集素材库（{chosen.length}）
                </Button>
              )}
            </div>
          </footer>
        </Frame>
      )}
    </>
  );
}

function ExtractionInlineFrame({ title, children }: ComponentProps<typeof Dialog>) {
  return (
    <section aria-label={title}>
      <h3 className="creation-inline-heading">{title}</h3>
      {children}
    </section>
  );
}
