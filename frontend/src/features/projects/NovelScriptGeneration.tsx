import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Spin } from 'antd';
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
  projectId, episodeId, modelId, novel, disabled, session,
}: {
  projectId: string;
  episodeId: string;
  contentVersion: string;
  modelId: string;
  novel: string;
  disabled: boolean;
  session: WritingSession;
}) {
  const api = storyboardApi(projectId, episodeId);
  const [instructions, setInstructions] = useState('');
  const [items, setItems] = useState<ScriptCandidate[]>([]);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [preview, setPreview] = useState<ScriptDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [scriptRevision, setScriptRevision] = useState(0);
  const [taskRevision, setTaskRevision] = useState(0);
  const completed = useRef(new Set<string>());

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    loadAllScripts(api, controller.signal)
      .then((scripts) => { if (!controller.signal.aborted) setItems(scripts); })
      .catch((cause) => { if (!controller.signal.aborted) setMessage(errorMessage(cause)); })
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
    }).catch((cause) => { if (!controller.signal.aborted) setMessage(errorMessage(cause)); });
    return () => controller.abort();
  }, [projectId, episodeId, taskRevision]);

  useEffect(() => {
    if (!tasks.some((task) => task.status === 'queued' || task.status === 'running')) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [tasks]);

  async function generate() {
    if (busy || disabled || !novel.trim()) return;
    setBusy(true); setMessage('');
    try {
      if (!await session.flush()) { setMessage('请先完成小说保存，再发起生成。'); return; }
      const latest = session.getSnapshot();
      const body = {
        ...(modelId ? { config_id: modelId } : {}),
        ...novelScriptRequest(projectId, episodeId, latest.contentVersion, instructions),
      };
      const scope = `novel-script:${projectId}:${episodeId}`;
      const idempotencyKey = await requestAttempt(scope, body, attemptStorage());
      const task = await generations.generateText(body, idempotencyKey);
      clearAttempt(scope, attemptStorage());
      setMessage(`剧本生成任务 ${task.generation_id} 已提交。`);
      setTaskRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function show(item: ScriptCandidate) {
    setBusy(true); setMessage('');
    try { setPreview(await api.script(item.id)); }
    catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function select(item: ScriptCandidate) {
    if (busy || !window.confirm('切换前会先保存当前剧本草稿。确定设为当前编辑剧本？')) return;
    setBusy(true); setMessage('');
    try {
      if (!await session.select(item.id)) setMessage('未能切换，当前草稿已保留。');
      else { setPreview(null); setScriptRevision((revision) => revision + 1); }
    } finally { setBusy(false); }
  }

  return <section className="generation-section">
    <h3>AI 生成剧本</h3>
    <Input.TextArea rows={2} maxLength={4000} value={instructions} disabled={disabled || busy} onChange={(event) => setInstructions(event.target.value)} placeholder="补充要求（选填）"/>
    <div className="dialog-actions"><Button type="primary" loading={busy} disabled={disabled || !novel.trim()} onClick={() => void generate()}>AI 生成剧本</Button><Button onClick={() => { setTaskRevision((revision) => revision + 1); setScriptRevision((revision) => revision + 1); }}>刷新状态与候选</Button></div>
    {message && <Alert type="info" showIcon message={message}/>}
    <h3>当前来源生成任务</h3>
    {tasks.map((task) => <div className="resource-import-row" key={task.generation_id}><span>{taskLabel(task)} · {task.generation_id}{task.error ? ` · ${task.error.message}` : ''}</span></div>)}
    <h3>候选剧本</h3>
    {loading ? <Spin/> : items.length ? <div>{items.map((item) => <div className="resource-import-row" key={item.id}>
      <div><strong>{item.is_editing ? '当前编辑 · ' : ''}{item.is_confirmed ? '已确认 · ' : ''}候选 {item.position}</strong><p>{item.preview || '暂无预览'}</p><small>{item.created_at ? new Date(item.created_at).toLocaleString() : '时间未知'}</small></div>
      <div><Button onClick={() => void show(item)}>预览</Button><Button type="primary" disabled={disabled || item.is_editing} onClick={() => void select(item)}>设为当前编辑</Button></div>
    </div>)}</div> : <p>暂无生成候选。</p>}
    {preview && <div className="storyboard-result-preview"><h4>候选全文预览</h4><pre>{preview.content}</pre><Button onClick={() => setPreview(null)}>关闭预览</Button></div>}
  </section>;
}
