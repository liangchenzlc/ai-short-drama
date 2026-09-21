import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Alert, Button, Checkbox, Empty, Input, Select, Spin, Tabs } from 'antd';
import { assetExtractionApi, type ExtractionCandidate, type ExtractionResult } from '../../api/modules/asset-extraction';
import type { AssetDraft, AssetKind } from '../../api/modules/assets';
import { generations } from '../../api/modules/generations';
import type { GenerationSummary } from '../../api/types/generations';
import { errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { EpisodeModelSelect } from './EpisodeModelSelect';
import type { WritingSession } from './writing-session';
import type { NavigationBarrier } from './writing-navigation';
import { defaultAdoption, extractionApplyRequest, scriptAssetsRequest, ExtractionReviewError } from './asset-extraction-contract';
import { savedConfigId } from '../ai-config/config-selection';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { dateLabel, generationError, taskLabel } from '../generations/presentation';

const labels: Record<AssetKind, string> = { character: '角色', scene: '场景', prop: '道具' };
const allKinds = Object.keys(labels) as AssetKind[];
const active = (task: GenerationSummary) => task.status === 'queued' || task.status === 'running';
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function ScriptAssetExtraction({ projectId, episodeId, session, readOnly, modelId, onModelChange, onApplied, onConfirmScript, registerBarrier }: {
  projectId: string; episodeId: string; session: WritingSession; readOnly: boolean; modelId: string;
  onModelChange: (id: string) => void; onApplied: () => void; onConfirmScript: () => void;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
}) {
  const writing = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const api = useMemo(() => assetExtractionApi(projectId, episodeId), [projectId, episodeId]);
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<'settings' | 'result' | 'history'>('settings');
  const [kinds, setKinds] = useState<AssetKind[]>(allKinds);
  const [instructions, setInstructions] = useState('');
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [activeId, setActiveId] = useState<string | null>(null);
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
  const actionLock = useRef(false);
  const task = tasks.find(item => item.generation_id === activeId);
  const dirty = !!result?.items.some(item => !item.applied && drafts[item.candidate_id] && !same(item.draft, drafts[item.candidate_id]));
  const invalid = !!result?.items.some(item => !item.applied && (() => {
    const draft = drafts[item.candidate_id] ?? item.draft;
    return !draft.name.trim() || !draft.description.trim() || !draft.prompt.trim();
  })());
  const canExtract = writing.loaded && writing.confirmed && !!writing.script.trim() && !readOnly;
  const stale = !!result && (result.stale || !writing.confirmed);
  const pending = tasks.some(active);

  const refreshTasks = useCallback(async (signal?: AbortSignal) => {
    try {
      const page = await generations.list({ service_type: 'text', project_id: projectId, episode_id: episodeId, source_scene: 'script_assets', offset: 0, limit: 20 }, signal);
      if (signal?.aborted) return;
      setTasks(previous => [...page.items, ...previous.filter(item => !page.items.some(next => next.generation_id === item.generation_id))]);
      setTotal(page.total); setHistoryError('');
      setActiveId(current => current ?? page.items[0]?.generation_id ?? null);
    } catch (cause) { if (!signal?.aborted) setHistoryError(errorMessage(cause)); }
  }, [projectId, episodeId]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshTasks(controller.signal);
    const timer = open || pending ? window.setInterval(() => { void refreshTasks(controller.signal); }, 4000) : undefined;
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [refreshTasks, open, pending]);

  function acceptResult(next: ExtractionResult, reset: boolean) {
    setResult(next); setDrafts(Object.fromEntries(next.items.map(item => [item.candidate_id, item.draft])));
    setChoices(previous => Object.fromEntries(next.items.map(item => {
      const old = previous[item.candidate_id];
      const before = result?.items.find(prior => prior.candidate_id === item.candidate_id);
      const changedMatches = !!before && !same(before.matches, item.matches);
      const choice = !reset && !changedMatches && (old === 'create' || item.matches.some(match => match.asset_id === old)) ? old : defaultAdoption(item);
      return [item.candidate_id, changedMatches && item.matches.length ? '' : choice];
    })));
    setSelected(previous => new Set(next.items.filter(item => !item.applied && (reset ? !!defaultAdoption(item) : previous.has(item.candidate_id))).map(item => item.candidate_id)));
    if (reset) { setTab(next.kinds[0] ?? 'character'); setEditing(null); }
  }

  useEffect(() => {
    if (!open || phase !== 'result' || !activeId || task?.status !== 'succeeded' || dirty) return;
    const controller = new AbortController(); setLoading(true); setError('');
    api.get(activeId, controller.signal).then(next => {
      if (!controller.signal.aborted) { acceptResult(next, result?.generation_id !== activeId); setLoading(false); }
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, open, phase, activeId, task?.status]);

  async function saveDrafts(): Promise<ExtractionResult | null> {
    if (!result || !dirty) return result;
    if (readOnly || invalid) throw new ExtractionReviewError('名称、描述和图片生成提示词均不能为空，请补充后保存。');
    const next = await api.save(result.generation_id, {
      result_version: result.result_version,
      items: result.items.filter(item => !item.applied && !same(item.draft, drafts[item.candidate_id])).map(item => ({ candidate_id: item.candidate_id, draft: drafts[item.candidate_id] })),
    });
    acceptResult(next, false);
    return next;
  }

  async function run(action: () => Promise<void>) {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(true); setError(''); setMessage('');
    try { await action(); } catch (cause) { setError(cause instanceof ExtractionReviewError ? cause.message : errorMessage(cause)); }
    finally { actionLock.current = false; setBusy(false); }
  }

  const barrierRef = useRef<NavigationBarrier>({ hasUnsettled: () => false, flush: async () => true });
  barrierRef.current = {
    hasUnsettled: () => dirty || actionLock.current,
    flush: async () => {
      if (actionLock.current) return false;
      let saved = false;
      await run(async () => { await saveDrafts(); saved = true; });
      return saved;
    },
  };
  useEffect(() => {
    registerBarrier({ hasUnsettled: () => barrierRef.current.hasUnsettled(), flush: () => barrierRef.current.flush() });
    return () => registerBarrier(null);
  }, [registerBarrier]);

  const close = () => { void run(async () => { await saveDrafts(); setOpen(false); }); };
  async function generate() {
    if (!canExtract || !kinds.length || pending) return;
    await run(async () => {
      await saveDrafts();
      if (!await session.flush()) throw new ExtractionReviewError('请先完成正文保存，再提取素材。');
      const latest = session.getSnapshot();
      if (!latest.confirmed || !latest.scriptId) throw new ExtractionReviewError('请先确认当前剧本。');
      const configId = savedConfigId(modelId);
      const body = { ...scriptAssetsRequest(projectId, episodeId, latest.scriptId, latest.contentVersion, kinds, instructions), ...(configId ? { config_id: configId } : {}) };
      const scope = `script-assets:${projectId}:${episodeId}`;
      const key = await requestAttempt(scope, body, attemptStorage());
      try {
        const receipt = await generations.generateText(body, key);
        clearAttempt(scope, attemptStorage()); setActiveId(receipt.generation_id); setResult(null); setDrafts({}); setPhase('result');
        await refreshTasks();
      } catch (cause) { throw new ExtractionReviewError(generationError(cause)); }
    });
  }

  async function adopt() {
    if (readOnly || stale) return;
    await run(async () => {
      const saved = await saveDrafts();
      if (!saved) return;
      if (result && !same(result.items.map(item => item.matches), saved.items.map(item => item.matches))) {
        throw new ExtractionReviewError('候选已保存，匹配建议有变化，请核对采用方式后再次加入。');
      }
      if (!await session.flush()) throw new ExtractionReviewError('请先完成正文保存。');
      const latest = session.getSnapshot();
      if (!latest.confirmed) throw new ExtractionReviewError('请先确认当前剧本。');
      const body = extractionApplyRequest(saved, selected, choices, latest.contentVersion);
      const scope = `script-assets-apply:${saved.generation_id}`;
      const key = await requestAttempt(scope, body, attemptStorage());
      const receipt = await api.apply(saved.generation_id, body, key);
      clearAttempt(scope, attemptStorage()); onApplied();
      setMessage(receipt.already_applied ? '这批素材已加入本集素材库。' : `已加入本集素材库：新增 ${receipt.created} 项，复用 ${receipt.reused} 项。`);
      acceptResult(await api.get(saved.generation_id), false); setEditing(null);
    });
  }

  async function reloadResult() {
    await run(async () => {
      if (dirty && !window.confirm('重新载入会放弃尚未保存的候选修改。确定继续？')) return;
      if (activeId) acceptResult(await api.get(activeId), true);
    });
  }

  async function showTask(id: string) {
    await run(async () => {
      await saveDrafts(); setActiveId(id); setResult(null); setDrafts({}); setEditing(null); setPhase('result');
    });
  }

  async function moreHistory() {
    await run(async () => {
      const page = await generations.list({ service_type: 'text', project_id: projectId, episode_id: episodeId, source_scene: 'script_assets', offset: tasks.length, limit: 20 });
      setTasks(previous => [...previous, ...page.items.filter(item => !previous.some(old => old.generation_id === item.generation_id))]); setTotal(page.total);
    });
  }

  function editDraft(item: ExtractionCandidate, patch: Partial<AssetDraft>) {
    setDrafts(previous => ({ ...previous, [item.candidate_id]: { ...(previous[item.candidate_id] ?? item.draft), ...patch } }));
    if (patch.name !== undefined || patch.kind !== undefined || patch.scene_time !== undefined) setChoices(previous => ({ ...previous, [item.candidate_id]: '' }));
  }

  const rows = result?.items.filter(item => (drafts[item.candidate_id] ?? item.draft).kind === tab) ?? [];
  const selectable = rows.filter(item => !item.applied);
  const chosen = result?.items.filter(item => !item.applied && selected.has(item.candidate_id)) ?? [];
  const selectItem = (id: string, checked: boolean) => setSelected(previous => { const next = new Set(previous); if (checked) next.add(id); else next.delete(id); return next; });
  const buttonLabel = pending ? '素材提取中' : tasks.some(item => item.status === 'succeeded') ? '查看提取结果' : 'AI 提取素材';

  return <>
    <Button onClick={() => { setOpen(true); setPhase(activeId ? 'result' : 'settings'); }}>{buttonLabel}</Button>
    {open && <Dialog title={phase === 'settings' ? '从剧本提取素材' : phase === 'history' ? '素材提取记录' : '素材提取结果'} className={`asset-extraction-dialog${phase === 'settings' ? ' is-settings' : ''}`} canClose={!busy} onClose={close}>
      <div className="extraction-body">
        {error && <Alert type="error" showIcon message={error} action={phase === 'result' && task?.status === 'succeeded' ? <Button disabled={busy} onClick={() => void reloadResult()}>重新载入结果</Button> : undefined}/>}
        {message && <Alert type="success" showIcon message={message}/>}
        {phase === 'settings' && <div className="extraction-settings">
          <p>从本集已确认剧本整理文字素材，核对后加入本集素材库。</p>
          {!canExtract && <Alert type="info" showIcon message={readOnly ? '当前为只读模式，可查看已有提取结果。' : !writing.loaded ? '正在等待剧本载入。' : '先确认剧本，再提取素材。'} action={!readOnly && writing.loaded ? <Button onClick={() => { setOpen(false); onConfirmScript(); }}>去确认剧本</Button> : undefined}/>}
          <fieldset disabled={busy || readOnly}><legend>提取范围</legend><Checkbox.Group aria-label="提取范围" value={kinds} options={allKinds.map(value => ({ value, label: labels[value] }))} onChange={values => setKinds(values as AssetKind[])}/></fieldset>
          <label className="writing-control"><span>补充要求 <small>选填</small></span><Input.TextArea aria-label="提取补充要求" rows={4} maxLength={4000} showCount value={instructions} disabled={busy || readOnly} onChange={event => setInstructions(event.target.value)} placeholder="例如：仅提取有台词的角色，保留推动剧情的重要道具。"/></label>
          <div className="writing-control"><span>文本模型</span><EpisodeModelSelect kind="text" label="素材提取文本模型" value={modelId} disabled={busy || readOnly} onChange={onModelChange}/></div>
          <p className="extraction-hint">提取名称、描述与图片生成提示词，此处不生成图片。</p>
          {pending && <Alert type="info" message="已有提取任务处理中，可以从提取记录查看进度。"/>}
        </div>}
        {phase === 'history' && <>
          {historyError && <Alert type="error" message={historyError} action={<Button onClick={() => void refreshTasks()}>重试</Button>}/>}
          {!tasks.length && !historyError && <Empty description="暂无提取记录"/>}
          {tasks.map(item => <div className="extraction-history-row" key={item.generation_id}><div><strong>{dateLabel(item.created_at)}</strong><p>{item.config?.name ?? '文本模型'} · {taskLabel(item)}</p></div><Button disabled={busy} onClick={() => void showTask(item.generation_id)}>查看</Button></div>)}
          {tasks.length < total && <Button disabled={busy} onClick={() => void moreHistory()}>加载更多记录</Button>}
        </>}
        {phase === 'result' && <>
          {loading && <div className="extraction-wait" role="status"><Spin/><p>正在载入提取结果…</p></div>}
          {!loading && (!task || active(task)) && <div className="extraction-wait" role="status"><Spin/><h3>{task ? taskLabel(task) : '正在查询任务'}</h3><p>正在整理角色、场景与道具。可以关闭弹窗，任务会继续处理。</p>{task?.can_cancel && <Button disabled={busy || readOnly} onClick={() => void run(async () => { await generations.cancel(task.generation_id); await refreshTasks(); })}>取消提取任务</Button>}{historyError && <Alert type="error" message={historyError} action={<Button onClick={() => void refreshTasks()}>重试查询</Button>}/>}</div>}
          {task && ['failed', 'cancelled'].includes(task.status) && <div className="extraction-wait"><Alert type={task.status === 'failed' ? 'error' : 'info'} showIcon message={task.error?.message ?? '提取任务已取消。'}/>{task.can_resume && <Button disabled={busy || readOnly} onClick={() => void run(async () => { await generations.resume(task.generation_id); await refreshTasks(); })}>恢复结果保存</Button>}<Button disabled={busy || readOnly || task.can_resume} onClick={() => setPhase('settings')}>调整要求并重新提取</Button></div>}
          {!loading && result?.generation_id === activeId && <>
            {stale && <Alert type="warning" showIcon message="来源剧本已变化，结果可查看；请确认当前剧本后重新提取。"/>}
            <Tabs activeKey={tab} onChange={key => setTab(key as AssetKind)} items={allKinds.map(kind => ({ key: kind, label: `${labels[kind]} ${result.items.filter(item => (drafts[item.candidate_id] ?? item.draft).kind === kind).length}${!result.kinds.includes(kind) ? '（未提取）' : ''}` }))}/>
            {rows.length > 0 && <div className="extraction-selection"><Checkbox disabled={busy || readOnly || stale || !selectable.length} checked={!!selectable.length && selectable.every(item => selected.has(item.candidate_id))} indeterminate={selectable.some(item => selected.has(item.candidate_id)) && !selectable.every(item => selected.has(item.candidate_id))} onChange={event => setSelected(previous => { const next = new Set(previous); for (const item of selectable) { if (event.target.checked) next.add(item.candidate_id); else next.delete(item.candidate_id); } return next; })}>本类全选</Checkbox><span>名称、描述与提示词均可编辑</span></div>}
            {!rows.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={result.kinds.includes(tab) ? `未提取到${labels[tab]}，可调整要求后重新提取。` : `本次未选择提取${labels[tab]}。`}/>}
            {rows.map(item => {
              const draft = drafts[item.candidate_id] ?? item.draft;
              const disabled = busy || readOnly || !!item.applied;
              return <article className="extraction-candidate" key={item.candidate_id}>
                <header><Checkbox aria-label={`选择 ${draft.name}`} disabled={disabled || stale} checked={selected.has(item.candidate_id) && !item.applied} onChange={event => selectItem(item.candidate_id, event.target.checked)}/><h3>{draft.name}</h3>{item.applied ? <span className="status-badge is-success">已加入本集</span> : <Button type="text" disabled={disabled} onClick={() => setEditing(editing === item.candidate_id ? null : item.candidate_id)}>{editing === item.candidate_id ? '收起编辑' : '编辑'}</Button>}</header>
                {editing === item.candidate_id ? <div className="extraction-editor">
                  <label>名称<Input aria-label="素材名称" maxLength={255} value={draft.name} disabled={disabled} onChange={event => editDraft(item, { name: event.target.value })}/></label>
                  <label>类型<Select aria-label="素材类型" value={draft.kind} disabled={disabled} options={allKinds.map(value => ({ value, label: labels[value] }))} onChange={kind => { editDraft(item, { kind, scene_time: kind === 'scene' ? draft.scene_time : '' }); setTab(kind); }}/></label>
                  <label>描述<Input.TextArea aria-label="素材描述" autoSize={{ minRows: 2, maxRows: 8 }} maxLength={8000} value={draft.description} disabled={disabled} onChange={event => editDraft(item, { description: event.target.value })}/></label>
                  <label>图片生成提示词<Input.TextArea aria-label="图片生成提示词" autoSize={{ minRows: 3, maxRows: 10 }} maxLength={8000} value={draft.prompt} disabled={disabled} onChange={event => editDraft(item, { prompt: event.target.value })}/></label>
                  {draft.kind === 'scene' && <label>场景时间<Input maxLength={60} value={draft.scene_time} disabled={disabled} onChange={event => editDraft(item, { scene_time: event.target.value })}/></label>}
                  <label>标签<Select aria-label="素材标签" mode="tags" value={draft.tags} disabled={disabled} maxCount={20} onChange={tags => editDraft(item, { tags })}/></label>
                </div> : <div className="extraction-copy"><div><h4>描述</h4><ExtractionText text={draft.description}/></div><div className="extraction-prompt"><div><h4>图片生成提示词</h4><Button size="small" type="text" onClick={() => void run(async () => { await navigator.clipboard.writeText(draft.prompt); setMessage(`已复制「${draft.name}」的图片生成提示词。`); })}>复制</Button></div><ExtractionText text={draft.prompt}/></div></div>}
                <details className="extraction-evidence"><summary>查看原文依据{item.original.aliases.length ? '与别名' : ''}</summary><blockquote>{item.original.evidence}</blockquote>{item.original.aliases.length > 0 && <p>别名：{item.original.aliases.join('、')}</p>}</details>
                {!item.applied && <div className="extraction-adoption"><label>采用方式<Select aria-label={`${draft.name}的采用方式`} disabled={disabled || dirty} value={choices[item.candidate_id] || undefined} placeholder={dirty ? '保存候选后核对匹配' : '请选择新建或复用'} options={[{ value: 'create', label: item.matches.length || item.duplicate_candidates?.length ? '明确另建素材' : '新建本集素材' }, ...item.matches.map(match => ({ value: match.asset_id, label: `复用${match.scope === 'episode' ? '本集' : '项目'}素材：${match.name}` }))]} onChange={value => setChoices(previous => ({ ...previous, [item.candidate_id]: value }))}/></label>{choices[item.candidate_id] && choices[item.candidate_id] !== 'create' && <p>沿用已有素材的描述、提示词与图片，提取文字保留在本次记录中。</p>}{item.matches.length > 0 && <p>{item.matches.length > 1 || !item.matches[0].exact ? '发现疑似重复素材，请核对后选择。' : '发现同名素材，可复用或明确另建。'}</p>}{!!item.duplicate_candidates?.length && <p>本次结果还有同名候选，请只选择需要的项，或修改名称后明确另建。</p>}</div>}
              </article>;
            })}
          </>}
        </>}
      </div>
      <footer className="extraction-footer">
        <div>{phase === 'result' && result ? <span>已选 <strong>{chosen.length}</strong> 项{dirty ? ' · 有未保存修改' : ''}</span> : <span>仅加入本集素材库</span>}<Button type="link" disabled={busy} onClick={() => setPhase(phase === 'history' ? activeId ? 'result' : 'settings' : 'history')}>{phase === 'history' ? '返回' : '提取记录'}</Button>{phase === 'result' && !readOnly && <Button type="link" disabled={busy || pending} onClick={() => setPhase('settings')}>重新提取</Button>}</div>
        <div>{phase === 'result' && dirty && <Button disabled={busy || invalid || readOnly} onClick={() => void run(async () => { await saveDrafts(); setMessage('候选修改已保存。'); })}>保存候选</Button>}<Button disabled={busy} onClick={close}>{phase === 'settings' ? '取消' : pending ? '后台处理' : '稍后处理'}</Button>{phase === 'settings' && <Button type="primary" loading={busy} disabled={!canExtract || !kinds.length || pending} onClick={() => void generate()}>AI 提取素材</Button>}{phase === 'result' && result && <Button type="primary" loading={busy} disabled={readOnly || stale || !chosen.length || invalid || chosen.some(item => !choices[item.candidate_id])} onClick={() => void adopt()}>加入本集素材库（{chosen.length}）</Button>}</div>
      </footer>
    </Dialog>}
  </>;
}

function ExtractionText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const long = text.length > 240 || text.split('\n').length > 6;
  return <><p className={long && !expanded ? 'extraction-text-clamped' : undefined}>{text}</p>{long && <Button type="link" size="small" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? '收起' : '展开全文'}</Button>}</>;
}
