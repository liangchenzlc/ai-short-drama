import { useEffect, useMemo, useRef, useState, type ReactNode, type Ref } from 'react';
import { Alert, Button, Skeleton, Tooltip } from 'antd';
import { History, Upload, FileText, Sparkles, PenLine } from 'lucide-react';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { Icon } from '../../components/ui/Icon';
import { storyboardApi, type ScriptCandidate, type ScriptDetail } from '../../api/modules/storyboard';
import { generations } from '../../api/modules/generations';
import type { GenerationReceipt, GenerationSummary } from '../../api/types/generations';
import { errorMessage } from '../../api/http';
import { attemptStorage, clearAttempt, getAttemptAccount, requestAttempt } from '../generations/attempt';
import { taskLabel } from '../generations/presentation';
import { novelScriptRequest } from './workflow-contract';
import type { WritingSession } from './writing-session';
import { decodeNovelFile } from './novel-import';
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
  source: 'novel' | 'synopsis' | 'import' | 'blank';
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

async function loadAllTasks(projectId: string, signal: AbortSignal) {
  const items: GenerationSummary[] = [];
  let offset = 0;
  let total = 1;
  while (offset < total) {
    const page = await generations.list({
      service_type: 'text', project_id: projectId,
      source_scene: 'novel_script', offset, limit: 100,
    }, signal);
    items.push(...page.items); total = page.total; offset += page.items.length;
    if (!page.items.length) break;
  }
  return items;
}

export function NovelScriptGeneration({
  projectId, novel, disabled, session, modelSelector, settings, onEditScript,
  draft, children, settingsActions, feedbackRef, onViewSource,
}: {
  projectId: string;

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
  feedbackRef?: Ref<HTMLDivElement>;
  onViewSource: () => void;
}) {
  const api = useMemo(() => storyboardApi(projectId), [projectId]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [source, setSource] = useState<'novel' | 'synopsis' | 'import' | 'blank'>(novel.trim() ? 'novel' : 'blank');
  const [startMode, setStartMode] = useState<'empty' | 'synopsis' | 'blank'>('empty');
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');
  const importInput = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<ScriptCandidate[]>([]);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [preview, setPreview] = useState<ScriptDetail | null>(null);
  const [showingCandidate, setShowingCandidate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [scriptsError, setScriptsError] = useState('');
  const [tasksError, setTasksError] = useState('');
  const [previewError, setPreviewError] = useState('');
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
  const attemptScope = creationScope(`novel-adaptation-attempt:${projectId}`);
  const pending = tasks.some(task => task.status === 'queued' || task.status === 'running') || !!submittedTask && (submittedTask.status === 'queued' || submittedTask.status === 'running');

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  function isCurrent() { return active.current && getAttemptAccount() === accountId; }

  function acceptReceipt(receipt: GenerationReceipt) {
    if (!isCurrent()) return false;
    clearRecovery(attemptScope);
    clearAttempt(`novel-script:${projectId}`, attemptStorage());
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
      if (!source || source.scene !== 'novel_script' || source.project_id !== projectId) throw new Error('生成恢复记录不属于当前作品。');
      setUncertain(restored); setMessageType('info'); setMessage('发现待核对的剧本生成请求，请核对原请求后继续。');
    } catch { setRecoveryBlocked(true); setMessageType('error'); setMessage('剧本生成恢复记录无法读取。请先核对任务中心的生成记录，当前已暂停新生成。'); }
  }, [projectId, attemptScope]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setScriptsError('');
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
      .catch((cause) => { if (!controller.signal.aborted) setScriptsError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, scriptRevision]);

  useEffect(() => {
    const controller = new AbortController();
    setTasksError('');
    loadAllTasks(projectId, controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      setTasks(next);
      setSubmittedTask(current => current && !next.some(task => task.generation_id === current.generation_id) ? current : null);
      const succeeded = next.filter((task) => task.status === 'succeeded' && !completed.current.has(task.generation_id));
      if (succeeded.length) {
        for (const task of succeeded) completed.current.add(task.generation_id);
        setScriptRevision((revision) => revision + 1);
      }
    }).catch((cause) => { if (!controller.signal.aborted) setTasksError(errorMessage(cause)); });
    return () => controller.abort();
  }, [projectId, taskRevision]);

  useEffect(() => {
    if (!pending) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [pending, tasks, submittedTask]);

  async function generate() {
    if (!isCurrent() || submissionLock.current || busy || pending || disabled || source !== 'novel' || !novel.trim() || !draft.value.input_text.trim() || !draft.loaded || recoveryBlocked) return;
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
      const request = novelScriptRequest(projectId, checked.contentVersion, savedDraft.input_text);
      const body = {
        ...(savedDraft.model_config_id ? { config_id: savedDraft.model_config_id } : {}),
        ...request,
        source: { ...request.source, draft_id: savedDraft.id, draft_version: savedDraft.draft_version },
      };
      const scope = `novel-script:${projectId}`;
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
        clearRecovery(attemptScope); clearAttempt(`novel-script:${projectId}`, attemptStorage()); setUncertain(null);
      }
      setMessageType('error'); setMessage(errorMessage(cause));
    }
    finally { submissionLock.current = false; if (isCurrent()) setSubmitting(false); }
  }

  async function show(item: ScriptCandidate) {
    setBusy(true); setPreviewError('');
    try { const detail = await api.script(item.id); if (isCurrent()) { setPreview(detail); setShowingCandidate(true); setHistoryOpen(false); } }
    catch (cause) { if (isCurrent()) setPreviewError(errorMessage(cause)); }
    finally { if (isCurrent()) setBusy(false); }
  }

  async function select(item: ScriptCandidate | ScriptDetail) {
    if (disabled || busy) return;
    if (!isCurrent()) return;
    setBusy(true); setMessage(''); setMessageType('error');
    try {
      if (item.id.startsWith('local-import-')) {
        const previous = session.getSnapshot();
        saveRecovery(creationScope(`writing-before-replace:${projectId}`), { novel: previous.novel, script: previous.script, editor_document: previous.editorDocument });
        session.edit('script', (item as ScriptDetail).content);
        const saved = await session.flush();
        if (!saved) { setMessage('导入文本已放入当前草稿，但保存未完成，请核对保存状态后重试。'); return; }
        setPreview(null); setShowingCandidate(false); setSource('import'); onEditScript();
        return;
      }
      const selected = await session.select(item.id);
      if (!isCurrent()) return;
      if (!selected) setMessage('未能切换，当前草稿已保留。');
      else { setPreview(null); setShowingCandidate(false); setHistoryOpen(false); setScriptRevision((revision) => revision + 1); onEditScript(); }
    } catch (cause) { if (isCurrent()) { setMessageType('error'); setMessage(errorMessage(cause)); } }
    finally { if (isCurrent()) setBusy(false); }
  }

  async function importFile(file?: File) {
    if (!file || importing || busy) return;
    setImporting(true); setImportError('');
    try {
      if (!file.name.toLowerCase().endsWith('.txt')) throw new Error('请选择 .txt 文件。');
      const content = decodeNovelFile(await file.arrayBuffer());
      if (isCurrent()) setImportText(content);
    } catch (cause) { if (isCurrent()) setImportError(cause instanceof Error ? cause.message : '文件读取失败，请重新选择。'); }
    finally { if (isCurrent()) setImporting(false); if (importInput.current) importInput.current.value = ''; }
  }
  function importPreview() {
    if (!importText.trim() || importing || busy) return;
    setPreview({ id: `local-import-${Date.now()}`, position: 0, state: 'unconfirmed', preview: importText.trim().slice(0, 180), is_editing: false, is_confirmed: false, generation_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), content: importText.trim() });
    setShowingCandidate(true); setImportOpen(false); setSource('import');
  }
  async function saveSynopsis() {
    if (!isCurrent() || disabled || busy || !draft.loaded || !draft.value.input_text.trim()) return;
    setBusy(true); setMessage('');
    try {
      const saved = await draft.flush();
      if (!isCurrent()) return;
      if (!saved) { setMessageType('error'); setMessage('梗概尚未保存，请核对左侧保存状态后重试。'); return; }
      setMessageType('info'); setMessage('梗概已保存。当前 AI 生成仅支持小说来源；可补充小说原文，或直接写空白剧本。');
    } catch (cause) { if (isCurrent()) { setMessageType('error'); setMessage(errorMessage(cause)); } }
    finally { if (isCurrent()) setBusy(false); }
  }
  const requestCheck = uncertain ? <Button aria-label="核对生成请求" aria-busy={submitting} loading={submitting} disabled={disabled || busy || submitting} onClick={async () => {
    if (submissionLock.current) return;
    submissionLock.current = true; setSubmitting(true);
    try { await resolveAttempt(uncertain); } finally { submissionLock.current = false; if (isCurrent()) setSubmitting(false); }
  }}>核对生成请求</Button> : null;
  const feedback = message ? <StudioFeedback type={messageType} title="剧本创作" message={message} persistent={!!uncertain || recoveryBlocked || messageType === 'error'} noticeKey={`novel-generation:${projectId}`} actions={requestCheck || (recoveryBlocked ? <Button onClick={() => setHistoryOpen(true)}>查看生成记录</Button> : undefined)}/> : null;
  const historyError = previewError || scriptsError || tasksError;
  const emptyState = !session.getSnapshot().script.trim() && !showingCandidate && startMode === 'empty' ? <section className="script-creation-empty" aria-label="开始创作当前作品剧本">
    <div className="script-creation-empty-copy"><h2>开始创作当前作品剧本</h2></div>
    <div className="script-source-cards">
      <button type="button" className="script-source-card" onClick={() => { setStartMode('synopsis'); setSource('synopsis'); setMessage(''); }}><Sparkles size={20}/><strong>从梗概创作</strong><span>记录人物、冲突和结尾</span><em>填写梗概</em></button>
      <button type="button" className="script-source-card" onClick={() => { setSource('novel'); setMessageType('info'); setMessage(novel.trim() ? '小说原文已准备好，请填写创作要求后生成。' : '请点击左侧「来源素材」中的「小说原文」写入或导入小说。'); }}><FileText size={20}/><strong>从小说改编</strong><span>按小说原文生成剧本</span><em>使用小说来源</em></button>
      <button type="button" className="script-source-card" onClick={() => { setImportOpen(true); setImportError(''); setSource('import'); }}><Upload size={20}/><strong>导入已有剧本</strong><span>粘贴文本或选择 TXT</span><em>导入剧本</em></button>
    </div>
    <button type="button" className="script-blank-link" onClick={() => { setSource('blank'); setStartMode('blank'); onEditScript(); }}><PenLine size={14}/>直接写空白剧本</button>
    {feedback}
  </section> : null;
  const synopsisState = startMode === 'synopsis' && !session.getSnapshot().script.trim() ? <section className="script-synopsis-panel" aria-label="从梗概创作"><Button type="text" onClick={() => { setStartMode('empty'); setSource('blank'); setMessage(''); }}>返回选择其他方式</Button><h2>从梗概创作</h2><label htmlFor="episode-script-synopsis">故事梗概</label><textarea id="episode-script-synopsis" disabled={disabled || busy || !draft.loaded} value={draft.value.input_text} maxLength={2000} onChange={event => draft.edit({ input_text: event.target.value })} placeholder="写下当前作品人物、核心冲突和结尾…"/><div className="script-synopsis-meta"><span>{draft.value.input_text.length} / 2000 字</span></div><Button type="primary" loading={busy} disabled={disabled || busy || !draft.loaded || !draft.value.input_text.trim()} onClick={() => void saveSynopsis()}>保存梗概</Button>{feedback}</section> : null;
  const history = <Tooltip title="生成记录"><Button type="text" size="small" aria-label="生成记录" icon={<History size={18}/>} onClick={() => setHistoryOpen(true)}/></Tooltip>;
  return <>
    <CreationSlot stage="source"><aside id="novel-adaptation-settings" className="writing-assistant novel-adaptation-settings" aria-label="剧本设置">
      <header><h3>剧本设置</h3><div className="novel-settings-heading-actions">{settingsActions}</div></header>
      <div className="novel-adaptation-body">{settings}
      {source === 'novel' && <GenerationComposer purpose="novel" prompt={draft.value.input_text} onPrompt={input_text => draft.edit({ input_text })} model={modelSelector} feedbackRef={feedbackRef}
        disabled={disabled || busy || submitting || !draft.loaded} busy={submitting || pending}
        sendDisabled={disabled || busy || pending || recoveryBlocked || !!uncertain || !draft.loaded || draft.status === 'conflict' || draft.status === 'error' || !novel.trim() || !draft.value.input_text.trim()} onSend={() => void generate()}/>}
      {draft.status === 'loading' && <span className="sr-only" role="status">正在读取创作设置…</span>}
      {(draft.status === 'error' || draft.status === 'conflict') && <StudioFeedback type="warning" quiet persistent title="剧本生成设置" message={draft.message} noticeKey={`novel-settings:${projectId}`} actions={<div className="novel-draft-actions">
        <Button size="small" onClick={() => {
          const url = URL.createObjectURL(new Blob([JSON.stringify(draft.value, null, 2)], { type: 'application/json' }));
          const link = document.createElement('a'); link.href = url; link.download = `episode-${projectId}-creation-draft.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
        }}>下载草稿</Button>
        {draft.status === 'error' && <Button size="small" onClick={() => void draft.retry()}>重试保存</Button>}
        <Button size="small" onClick={() => { try { saveRecovery(creationScope(`generation-before-replace:${projectId}`), draft.value); void draft.reload(); } catch (cause) { setMessageType('error'); setMessage(errorMessage(cause)); } }}>使用服务端设置</Button>
      </div>}/>}
      {pending && <span className="sr-only" role="status">剧本正在生成…</span>}
      {source === 'novel' ? <button type="button" className="script-source-summary script-source-trigger" aria-label="查看原文" aria-haspopup="dialog" onClick={onViewSource}>
        <span>来源素材</span><strong>小说原文 · {novel.length.toLocaleString()} 字</strong>
      </button> : <div className="script-source-summary" title={source === 'synopsis' ? 'AI 生成目前仅支持小说来源。' : source === 'import' ? '导入内容先预览，采用后保存为当前稿。' : undefined}><span>来源素材</span><strong>{source === 'synopsis' ? `故事梗概 · ${draft.value.input_text.length.toLocaleString()} 字` : source === 'import' ? '外部导入 · 本次会话' : '空白剧本 · 手写'}</strong>
        <button type="button" className="script-source-original" aria-label="查看原文" aria-haspopup="dialog" onClick={() => { if (source === 'blank') setSource('novel'); onViewSource(); }}>小说原文 · {novel.length.toLocaleString()} 字</button>
      </div>}
      </div>
    </aside></CreationSlot>
    {synopsisState || emptyState || children({ candidate: preview, showingCandidate, busy, showCandidate: setShowingCandidate,
      adopt: () => { if (preview) void select(preview); }, history, feedback, source })}
    {historyError && <StudioFeedback type="error" quiet persistent title="剧本生成记录" message={historyError} noticeKey={`novel-history:${projectId}`} actions={<Button onClick={() => setHistoryOpen(true)}>查看生成记录</Button>}/>}
    {importOpen && <Dialog title="导入已有剧本" className="standard-creation-dialog script-import-dialog" canClose={!busy && !importing} onClose={() => setImportOpen(false)}>
      <div className="script-import-body"><p>导入后先只读预览，采用后保存为当前稿。</p>
      <label className="script-import-text"><span>剧本文本</span><textarea value={importText} disabled={busy || importing} maxLength={1048576} onChange={event => { setImportText(event.target.value); setImportError(''); }} placeholder="粘贴当前作品剧本内容…"/></label>
      {importError && <Alert type="error" showIcon message={importError}/>}</div>
      <footer className="script-import-actions"><span>{importText.length.toLocaleString()} 字</span><Button loading={importing} disabled={busy || importing} icon={<Upload size={15}/>} onClick={() => importInput.current?.click()}>{importing ? '读取中…' : '选择 TXT'}</Button><input hidden ref={importInput} disabled={busy || importing} type="file" accept=".txt,text/plain" aria-label="导入剧本文件" onChange={event => void importFile(event.target.files?.[0])}/>
        <Button disabled={busy || importing} onClick={() => setImportOpen(false)}>取消</Button><Button type="primary" disabled={busy || importing || !importText.trim()} onClick={importPreview}>预览导入内容</Button></footer>
    </Dialog>}
    {historyOpen && <Dialog title="剧本生成记录" className="standard-creation-dialog script-preview-dialog" canClose={!busy} onClose={() => setHistoryOpen(false)}><section className="writing-candidates" aria-label="候选剧本">
      <header><div><h3>候选剧本 <span>{items.length}</span></h3></div><Button loading={loading} disabled={busy || loading} onClick={() => { setPreviewError(''); setTaskRevision(revision => revision + 1); setScriptRevision(revision => revision + 1); }}>刷新结果</Button></header>
      {historyError && <Alert type="error" showIcon message={historyError}/>}
      {loading ? <Skeleton title paragraph={{ rows: 2 }}/> : items.length ? <div className="script-candidate-list">{items.map(item => <article className={`script-candidate${item.is_editing ? ' is-current' : ''}`} key={item.id}>
        <div className="candidate-copy"><h4>剧本 {item.position} {item.is_editing && <span className="status-badge">当前编辑</span>}</h4><p>{item.preview || '暂无正文预览'}</p><time>{item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : '时间未知'}</time></div>
        <div className="candidate-actions"><Button disabled={busy} onClick={() => void show(item)}>预览全文</Button>{item.is_editing && <Button onClick={() => { setHistoryOpen(false); onEditScript(); }}>继续编辑</Button>}</div>
      </article>)}</div> : <div className="candidate-empty"><Icon name="film" size={28}/><div><strong>暂无候选剧本</strong><p>生成完成后在这里查看。</p></div></div>}
      {tasks.length > 0 && <details className="writing-task-history"><summary>生成记录（{tasks.length}）</summary>{tasks.map(task => <div className="writing-task-row" key={task.generation_id}><span className={`status-badge status-${task.status}`}>{taskLabel(task)}</span><span>{task.error?.message || `任务 ${task.generation_id}`}</span></div>)}</details>}
    </section></Dialog>}
  </>;
}
