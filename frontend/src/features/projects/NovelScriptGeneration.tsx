import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Input, Skeleton } from 'antd';
import { Dialog } from '../../components/ui/Dialog';
import { Icon } from '../../components/ui/Icon';
import { storyboardApi, type ScriptCandidate, type ScriptDetail } from '../../api/modules/storyboard';
import { generations } from '../../api/modules/generations';
import type { GenerationSummary } from '../../api/types/generations';
import { errorMessage } from '../../api/http';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { taskLabel } from '../generations/presentation';
import { novelScriptRequest } from './workflow-contract';
import type { WritingSession } from './writing-session';

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
  projectId, episodeId, modelId, novel, disabled, session, modelSelector, settings, onEditScript,
}: {
  projectId: string;
  episodeId: string;
  contentVersion: string;
  modelId: string;
  novel: string;
  disabled: boolean;
  session: WritingSession;
  modelSelector: ReactNode;
  settings?: ReactNode;
  onEditScript: () => void;
}) {
  const api = storyboardApi(projectId, episodeId);
  const [instructions, setInstructions] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [items, setItems] = useState<ScriptCandidate[]>([]);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [preview, setPreview] = useState<ScriptDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState<'error' | 'success' | 'info'>('error');
  const [scriptRevision, setScriptRevision] = useState(0);
  const [taskRevision, setTaskRevision] = useState(0);
  const completed = useRef(new Set<string>());

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    loadAllScripts(api, controller.signal)
      .then((scripts) => { if (!controller.signal.aborted) setItems(scripts); })
      .catch((cause) => { if (!controller.signal.aborted) { setMessageType('error'); setMessage(errorMessage(cause)); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, episodeId, scriptRevision]);

  useEffect(() => {
    const controller = new AbortController();
    loadAllTasks(projectId, episodeId, controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      setTasks(next);
      const succeeded = next.filter((task) => task.status === 'succeeded' && !completed.current.has(task.generation_id));
      if (succeeded.length) {
        for (const task of succeeded) completed.current.add(task.generation_id);
        setScriptRevision((revision) => revision + 1);
      }
    }).catch((cause) => { if (!controller.signal.aborted) { setMessageType('error'); setMessage(errorMessage(cause)); } });
    return () => controller.abort();
  }, [projectId, episodeId, taskRevision]);

  useEffect(() => {
    if (!tasks.some((task) => task.status === 'queued' || task.status === 'running')) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [tasks]);

  async function generate() {
    if (busy || disabled || !novel.trim()) return;
    setBusy(true); setMessage(''); setMessageType('error');
    try {
      if (!await session.flush()) { setMessage('请先完成小说保存，再发起生成。'); return; }
      const latest = session.getSnapshot();
      const body = {
        ...(modelId ? { config_id: modelId } : {}),
        ...novelScriptRequest(projectId, episodeId, latest.contentVersion, instructions),
      };
      const scope = `novel-script:${projectId}:${episodeId}`;
      const idempotencyKey = await requestAttempt(scope, body, attemptStorage());
      await generations.generateText(body, idempotencyKey);
      clearAttempt(scope, attemptStorage());
      setMessageType('success'); setMessage('已开始生成。你可以继续编辑，完成后可在生成记录中预览采用。');
      setTaskRevision((revision) => revision + 1);
    } catch (cause) { setMessageType('error'); setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function show(item: ScriptCandidate) {
    setBusy(true); setMessage(''); setMessageType('error');
    try { setPreview(await api.script(item.id)); }
    catch (cause) { setMessageType('error'); setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function select(item: ScriptCandidate) {
    if (disabled || busy || !window.confirm('切换前会先保存当前剧本草稿。确定设为当前编辑剧本？')) return;
    setBusy(true); setMessage(''); setMessageType('error');
    try {
      if (!await session.select(item.id)) setMessage('未能切换，当前草稿已保留。');
      else { setPreview(null); setHistoryOpen(false); setScriptRevision((revision) => revision + 1); onEditScript(); }
    } catch (cause) { setMessageType('error'); setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  return <>
    <aside className="writing-assistant" aria-label="剧本改编设置">
      <h3><Icon name="film" size={20}/>AI 剧本改编</h3>
      <p>将原文转为适合短剧的场景与对白，生成后由你选择采用。</p>
      <label className="writing-control"><span>剧本生成模型</span>{modelSelector}</label>
      {settings}
      <label className="writing-control"><span>改编要求 <small>选填</small></span><Input.TextArea rows={4} maxLength={4000} value={instructions} disabled={disabled || busy} onChange={(event) => setInstructions(event.target.value)} placeholder="例如：突出主角冲突，保留关键对白，结尾设置悬念。"/></label>
      <Button block type="primary" loading={busy} disabled={disabled || !novel.trim()} onClick={() => void generate()}>生成剧本</Button>
      <Button block onClick={() => setHistoryOpen(true)}>生成记录</Button>
      <p className="writing-assistant-hint">{novel.trim() ? '使用所选模型生成，可能产生模型服务费用。' : '先写入小说原文，再开始生成。'}</p>
      {message && <Alert type={messageType} showIcon message={message}/>}
      {tasks.some(task => task.status === 'running' || task.status === 'queued') && <p role="status">任务处理中，完成后自动更新候选。</p>}
    </aside>
    {historyOpen && <Dialog title="剧本生成记录" className="script-preview-dialog" canClose={!busy} onClose={() => setHistoryOpen(false)}><section className="writing-candidates" aria-label="候选剧本">
      <header><div><h3>候选剧本 <span>{items.length}</span></h3><p>预览、比较，选中适合本集的一稿继续编辑。</p></div><Button disabled={busy || loading} onClick={() => { setTaskRevision(revision => revision + 1); setScriptRevision(revision => revision + 1); }}>刷新结果</Button></header>
      {loading ? <Skeleton title paragraph={{ rows: 2 }}/> : items.length ? <div className="script-candidate-list">{items.map(item => <article className={`script-candidate${item.is_editing ? ' is-current' : ''}`} key={item.id}>
        <div className="candidate-copy"><h4>剧本 {item.position} {item.is_editing && <span className="status-badge">当前编辑</span>} {item.is_confirmed && <span className="status-badge is-success">已确认</span>}</h4><p>{item.preview || '暂无正文预览'}</p><time>{item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : '时间未知'}</time></div>
        <div className="candidate-actions"><Button disabled={busy} onClick={() => void show(item)}>预览全文</Button>{item.is_editing && <Button onClick={() => { setHistoryOpen(false); onEditScript(); }}>继续编辑</Button>}</div>
      </article>)}</div> : <div className="candidate-empty"><Icon name="film" size={28}/><div><strong>让故事多一种表达</strong><p>生成的剧本会出现在这里，原稿会保留。</p></div></div>}
      {tasks.length > 0 && <details className="writing-task-history"><summary>生成记录（{tasks.length}）</summary>{tasks.map(task => <div className="writing-task-row" key={task.generation_id}><span className={`status-badge status-${task.status}`}>{taskLabel(task)}</span><span>{task.error?.message || `任务 ${task.generation_id}`}</span></div>)}</details>}
    </section></Dialog>}
    {preview && <Dialog title="候选剧本预览" className="script-preview-dialog" canClose={!busy} onClose={() => setPreview(null)}><div className="script-preview-body">{message && <Alert type={messageType} showIcon message={message}/>}<pre>{preview.content}</pre></div><div className="dialog-actions"><Button disabled={busy} onClick={() => setPreview(null)}>关闭</Button>{items.find(item => item.id === preview.id)?.is_editing ? <Button type="primary" onClick={() => { setPreview(null); setHistoryOpen(false); onEditScript(); }}>继续编辑</Button> : <Button type="primary" loading={busy} disabled={disabled || busy || !items.some(item => item.id === preview.id)} onClick={() => { const item = items.find(item => item.id === preview.id); if (item) void select(item); }}>采用并编辑</Button>}</div></Dialog>}
  </>;
}
