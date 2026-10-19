import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Skeleton, Tooltip } from 'antd';
import { History } from 'lucide-react';
import { Dialog } from '../../components/ui/Dialog';
import { Icon } from '../../components/ui/Icon';
import { storyboardApi, type ScriptCandidate, type ScriptDetail } from '../../api/modules/storyboard';
import { generations } from '../../api/modules/generations';
import type { GenerationReceipt, GenerationSummary } from '../../api/types/generations';
import { errorMessage } from '../../api/http';
import { attemptStorage, clearAttempt, getAttemptAccount, requestAttempt } from '../generations/attempt';
import { taskLabel } from '../generations/presentation';
import { novelScriptRequest } from './workflow-contract';
import type { WritingSession } from './writing-session';
import type { useCreationDraft } from './useCreationDraft';
import { GenerationComposer } from './GenerationComposer';
import { CreationSlot } from './EpisodeCreationWorkspace';
import { creationScope, clearRecovery, readRecovery, saveRecovery } from './creation-recovery';
import { isNovelGenerationAttempt, type NovelGenerationAttempt } from './novel-generation-attempt';
import './workflow-refinement.css';

export interface NovelGenerationView {
  candidate: ScriptDetail | null; showingCandidate: boolean; busy: boolean;
  showCandidate: (show: boolean) => void; adopt: () => void;
  history: ReactNode; feedback: ReactNode;
}

async function loadAllScripts(api: ReturnType<typeof storyboardApi>, signal: AbortSignal) {
  const items: ScriptCandidate[] = [];
  let offset = 0;
  let total = 1;
  while (offset < total) {
    const page = await api.scripts(signal, offset);
    items.push(...page.items); total = page.total; offset += page.items.length;
    if (!page.items.length) break;
  }
  return items;
}

async function loadAllTasks(projectId: string, episodeId: string, signal: AbortSignal) {
  const items: GenerationSummary[] = [];
  let offset = 0;
  let total = 1;
  while (offset < total) {
    const page = await generations.list({
      service_type: 'text', project_id: projectId, episode_id: episodeId,
      source_scene: 'novel_script', offset, limit: 100,
    }, signal);
    items.push(...page.items); total = page.total; offset += page.items.length;
    if (!page.items.length) break;
  }
  return items;
}

export function NovelScriptGeneration({
  projectId, episodeId, novel, disabled, session, modelSelector, settings, onEditScript,
  draft, children, settingsActions,
}: {
  projectId: string;
  episodeId: string;
  contentVersion: string;
  modelId?: string;
  novel: string;
  disabled: boolean;
  session: WritingSession;
  modelSelector: ReactNode;
  settings?: ReactNode;
  onEditScript: () => void;
  draft: ReturnType<typeof useCreationDraft>;
  children: (view: NovelGenerationView) => ReactNode;
  settingsActions?: ReactNode;
}) {
  const api = useMemo(() => storyboardApi(projectId, episodeId), [projectId, episodeId]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [items, setItems] = useState<ScriptCandidate[]>([]);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [preview, setPreview] = useState<ScriptDetail | null>(null);
  const [showingCandidate, setShowingCandidate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submittedTask, setSubmittedTask] = useState<GenerationReceipt | null>(null);
  const [uncertain, setUncertain] = useState<NovelGenerationAttempt | null>(null);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const submissionLock = useRef(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState<'error' | 'success' | 'info'>('error');
  const [scriptRevision, setScriptRevision] = useState(0);
  const [taskRevision, setTaskRevision] = useState(0);
  const completed = useRef(new Set<string>());
  const currentGeneration = useRef<string | null>(null);
  const active = useRef(true);
  const accountId = getAttemptAccount();
  const attemptScope = creationScope(`novel-adaptation-attempt:${projectId}:${episodeId}`);
  const pending = tasks.some(task => task.status === 'queued' || task.status === 'running') || !!submittedTask && (submittedTask.status === 'queued' || submittedTask.status === 'running');

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  function isCurrent() { return active.current && getAttemptAccount() === accountId; }

  function acceptReceipt(receipt: GenerationReceipt) {
    if (!isCurrent()) return false;
    clearRecovery(attemptScope);
    clearAttempt(`novel-script:${projectId}:${episodeId}`, attemptStorage());
    setUncertain(null); setRecoveryBlocked(false); setSubmittedTask(receipt);
    currentGeneration.current = receipt.generation_id;
    setTaskRevision(revision => revision + 1);
    return true;
  }

  async function resolveAttempt(attempt: NovelGenerationAttempt) {
    try {
      const receipt = await generations.lookup('text', attempt.key);
      if (!isCurrent()) return;
      if (receipt) { acceptReceipt(receipt); setMessageType('success'); setMessage('已找到原生成任务，将继续读取同一任务的结果。'); }
      else { setMessageType('info'); setMessage('暂未查到原生成任务。请求仍待核对，请稍后再次核对；当前不会重新生成。'); }
    } catch (cause) { if (isCurrent()) { setMessageType('error'); setMessage(`无法核对原生成请求：${errorMessage(cause)}`); } }
  }

  useEffect(() => {
    try {
      const restored = readRecovery(attemptScope, isNovelGenerationAttempt);
      if (!restored) return;
      const source = restored.body.source;
      if (!source || source.scene !== 'novel_script' || source.project_id !== projectId || source.episode_id !== episodeId) throw new Error('生成恢复记录不属于当前分集。');
      setUncertain(restored); setMessageType('info'); setMessage('发现待核对的剧本生成请求，请核对原请求后继续。');
    } catch { setRecoveryBlocked(true); setMessageType('error'); setMessage('剧本生成恢复记录无法读取。请先核对任务中心的生成记录，当前已暂停新生成。'); }
  }, [projectId, episodeId, attemptScope]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    loadAllScripts(api, controller.signal)
      .then(async (scripts) => {
        if (controller.signal.aborted) return;
        setItems(scripts);
        const latest = scripts.find(item => item.generation_id === currentGeneration.current);
        if (latest) {
          const detail = await api.script(latest.id, controller.signal);
          if (controller.signal.aborted) return;
          currentGeneration.current = null; setPreview(detail); setShowingCandidate(true);
        }
      })
      .catch((cause) => { if (!controller.signal.aborted) { setMessageType('error'); setMessage(errorMessage(cause)); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, episodeId, scriptRevision]);

  useEffect(() => {
    const controller = new AbortController();
    loadAllTasks(projectId, episodeId, controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      setTasks(next);
      setSubmittedTask(current => current && !next.some(task => task.generation_id === current.generation_id) ? current : null);
      const succeeded = next.filter((task) => task.status === 'succeeded' && !completed.current.has(task.generation_id));
      if (succeeded.length) {
        for (const task of succeeded) completed.current.add(task.generation_id);
        setScriptRevision((revision) => revision + 1);
      }
    }).catch((cause) => { if (!controller.signal.aborted) { setMessageType('error'); setMessage(errorMessage(cause)); } });
    return () => controller.abort();
  }, [projectId, episodeId, taskRevision]);

  useEffect(() => {
    if (!pending) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [pending, tasks, submittedTask]);

  async function generate() {
    if (!isCurrent() || submissionLock.current || busy || pending || disabled || !novel.trim() || !draft.loaded || recoveryBlocked) return;
    submissionLock.current = true;
    setSubmitting(true); setMessage(''); setMessageType('error');
    try {
      if (uncertain) { await resolveAttempt(uncertain); return; }
      const savedNovel = await session.flush();
      if (!isCurrent()) return;
      if (!savedNovel) { setMessage('请先完成小说保存，再发起生成。'); return; }
      const latest = session.getSnapshot();
      const savedDraft = await draft.flush();
      if (!isCurrent()) return;
      if (!savedDraft) { setMessage('请先保存改编要求和文本模型，再发起生成。'); return; }
      const checked = session.getSnapshot();
      if (checked.dirty || checked.contentVersion !== latest.contentVersion || draft.session.hasUnsettled()) {
        setMessage('保存期间内容发生变化，请核对最新内容后重新生成。'); return;
      }
      const request = novelScriptRequest(projectId, episodeId, checked.contentVersion, savedDraft.input_text);
      const body = {
        ...(savedDraft.model_config_id ? { config_id: savedDraft.model_config_id } : {}),
        ...request,
        source: { ...request.source, draft_id: savedDraft.id, draft_version: savedDraft.draft_version },
      };
      const scope = `novel-script:${projectId}:${episodeId}`;
      const idempotencyKey = await requestAttempt(scope, body, attemptStorage());
      if (!isCurrent()) return;
      const attempt: NovelGenerationAttempt = { format: 1, key: idempotencyKey, body };
      saveRecovery(attemptScope, attempt);
      setUncertain(attempt);
      const receipt = await generations.generateText(body, idempotencyKey);
      if (!acceptReceipt(receipt)) return;
      setMessageType('success'); setMessage('已开始生成，完成后将在正文区只读预览。');
      setTaskRevision((revision) => revision + 1);
    } catch (cause) {
      if (!isCurrent()) return;
      const status = (cause as { status?: number }).status;
      if (status && status >= 400 && status < 500 && status !== 409) {
        clearRecovery(attemptScope); clearAttempt(`novel-script:${projectId}:${episodeId}`, attemptStorage()); setUncertain(null);
      }
      setMessageType('error'); setMessage(errorMessage(cause));
    }
    finally { submissionLock.current = false; if (isCurrent()) setSubmitting(false); }
  }

  async function show(item: ScriptCandidate) {
    setBusy(true); setMessage(''); setMessageType('error');
    try { const detail = await api.script(item.id); if (isCurrent()) { setPreview(detail); setShowingCandidate(true); setHistoryOpen(false); } }
    catch (cause) { if (isCurrent()) { setMessageType('error'); setMessage(errorMessage(cause)); } }
    finally { if (isCurrent()) setBusy(false); }
  }

  async function select(item: ScriptCandidate) {
    if (disabled || busy) return;
    if (!isCurrent()) return;
    setBusy(true); setMessage(''); setMessageType('error');
    try {
      const selected = await session.select(item.id);
      if (!isCurrent()) return;
      if (!selected) setMessage('未能切换，当前草稿已保留。');
      else { setPreview(null); setShowingCandidate(false); setHistoryOpen(false); setScriptRevision((revision) => revision + 1); onEditScript(); }
    } catch (cause) { if (isCurrent()) { setMessageType('error'); setMessage(errorMessage(cause)); } }
    finally { if (isCurrent()) setBusy(false); }
  }

  const history = <Tooltip title="生成记录"><Button type="text" size="small" aria-label="生成记录" icon={<History size={18}/>} onClick={() => setHistoryOpen(true)}/></Tooltip>;
  const feedback = message ? <Alert className="novel-generation-feedback" type={messageType} showIcon message={message}/> : null;
  return <>
    <CreationSlot stage="source"><aside id="novel-adaptation-settings" className="writing-assistant novel-adaptation-settings" aria-label="剧本改编设置">
      <header><h3>剧本改编设置</h3><div className="novel-settings-heading-actions">{settingsActions}</div></header>
      <div className="novel-adaptation-body">{settings}
      <GenerationComposer purpose="novel" prompt={draft.value.input_text} onPrompt={input_text => draft.edit({ input_text })} model={modelSelector}
        disabled={disabled || busy || submitting || !draft.loaded} busy={submitting || pending}
        sendDisabled={disabled || busy || pending || recoveryBlocked || !!uncertain || !draft.loaded || draft.status === 'conflict' || draft.status === 'error' || !novel.trim()} onSend={() => void generate()}/>
      {uncertain && <Button className="novel-uncertain-action" aria-label="核对生成请求" aria-busy={submitting} loading={submitting} disabled={disabled || busy || submitting} onClick={async () => {
        if (submissionLock.current) return;
        submissionLock.current = true; setSubmitting(true);
        try { await resolveAttempt(uncertain); } finally { submissionLock.current = false; if (isCurrent()) setSubmitting(false); }
      }}>核对生成请求</Button>}
      {!novel.trim() && <p className="writing-assistant-hint">先在“查看原文”中写入或导入小说。</p>}
      {draft.status === 'loading' && <p role="status">正在读取改编设置…</p>}
      {(draft.status === 'error' || draft.status === 'conflict') && <Alert className="novel-draft-feedback" type="warning" showIcon message={draft.message} action={<div className="novel-draft-actions">
        <Button size="small" onClick={() => {
          const url = URL.createObjectURL(new Blob([JSON.stringify(draft.value, null, 2)], { type: 'application/json' }));
          const link = document.createElement('a'); link.href = url; link.download = `episode-${episodeId}-adaptation-draft.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
        }}>下载草稿</Button>
        {draft.status === 'error' && <Button size="small" onClick={() => void draft.retry()}>重试保存</Button>}
        <Button size="small" onClick={() => { try { saveRecovery(creationScope(`generation-before-replace:${projectId}:${episodeId}`), draft.value); void draft.reload(); } catch (cause) { setMessageType('error'); setMessage(errorMessage(cause)); } }}>使用服务端设置</Button>
      </div>}/>}
      {pending && <p role="status">剧本正在生成…</p>}</div>
    </aside></CreationSlot>
    {children({ candidate: preview, showingCandidate, busy, showCandidate: setShowingCandidate,
      adopt: () => { if (preview) void select(preview); }, history, feedback })}
    {historyOpen && <Dialog title="剧本生成记录" className="script-preview-dialog" canClose={!busy} onClose={() => setHistoryOpen(false)}><section className="writing-candidates" aria-label="候选剧本">
      <header><div><h3>候选剧本 <span>{items.length}</span></h3><p>预览、比较，选中适合本集的一稿继续编辑。</p></div><Button disabled={busy || loading} onClick={() => { setTaskRevision(revision => revision + 1); setScriptRevision(revision => revision + 1); }}>刷新结果</Button></header>
      {loading ? <Skeleton title paragraph={{ rows: 2 }}/> : items.length ? <div className="script-candidate-list">{items.map(item => <article className={`script-candidate${item.is_editing ? ' is-current' : ''}`} key={item.id}>
        <div className="candidate-copy"><h4>剧本 {item.position} {item.is_editing && <span className="status-badge">当前编辑</span>}</h4><p>{item.preview || '暂无正文预览'}</p><time>{item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : '时间未知'}</time></div>
        <div className="candidate-actions"><Button disabled={busy} onClick={() => void show(item)}>预览全文</Button>{item.is_editing && <Button onClick={() => { setHistoryOpen(false); onEditScript(); }}>继续编辑</Button>}</div>
      </article>)}</div> : <div className="candidate-empty"><Icon name="film" size={28}/><div><strong>让故事多一种表达</strong><p>生成的剧本会出现在这里，原稿会保留。</p></div></div>}
      {tasks.length > 0 && <details className="writing-task-history"><summary>生成记录（{tasks.length}）</summary>{tasks.map(task => <div className="writing-task-row" key={task.generation_id}><span className={`status-badge status-${task.status}`}>{taskLabel(task)}</span><span>{task.error?.message || `任务 ${task.generation_id}`}</span></div>)}</details>}
    </section></Dialog>}
  </>;
}
