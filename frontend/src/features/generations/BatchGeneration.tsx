import { confirmAction } from '../../components/ui/confirm';
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Drawer, Input, Pagination, Radio, Select, Spin, Table, Tag } from 'antd';
import { Link } from 'react-router-dom';
import { generationBatches, type Batch, type BatchDetail, type BatchPreview, type BatchRequest, type BatchScene, type BatchScope } from '../../api/modules/generation-batches';
import { errorMessage } from '../../api/http';
import { ConfigSelect } from './ConfigSelect';
import { TaskDetail } from './TaskDetail';
import { attemptStorage, clearAttempt, requestAttempt } from './attempt';
import { episodePath } from '../../app/paths';
import '../../app/batches.css';

const labels: Record<string, string> = { asset_image: '素材图片', shot_image: '分镜图片', shot_video: '分镜视频',
  waiting: '等待执行', active: '执行中', running: '执行中', paused: '已暂停', blocked: '依赖待处理',
  needs_review: '受理待核对', succeeded: '成功', partial: '部分成功', failed: '失败', cancelled: '已取消',
  ready: '可执行', adopted: '已有采用', review: '待审核' };
const ongoing = (status: string) => ['running', 'paused', 'needs_review', 'cancelled'].includes(status);

export function useBatchSelection(scope: string) {
  const [enabled, setEnabled] = useState(false);
  const [ids, setIds] = useState<string[]>([]);
  useEffect(() => { setIds([]); }, [scope]);
  useEffect(() => {
    const controller = new AbortController();
    void generationBatches.capabilities(controller.signal).then(result => { if (!controller.signal.aborted) setEnabled(result.enabled); }).catch(() => {});
    return () => controller.abort();
  }, []);
  return { enabled, ids, setIds, toggle: (id: string, checked: boolean) => setIds(current => checked ? [...new Set([...current, id])] : current.filter(value => value !== id)) };
}

export function BatchLauncher({ scope, selection, loadedIds, disabled, beforePreflight }: {
  scope: BatchScope; selection: ReturnType<typeof useBatchSelection>; loadedIds: string[]; disabled?: boolean;
  beforePreflight?: () => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false), [all, setAll] = useState(false);
  if (!selection.enabled) return null;
  return <div className="batch-toolbar">
    <Checkbox disabled={disabled || !loadedIds.length} checked={!!loadedIds.length && loadedIds.every(id => selection.ids.includes(id))}
      onChange={event => selection.setIds(event.target.checked ? [...new Set([...selection.ids, ...loadedIds])] : selection.ids.filter(id => !loadedIds.includes(id)))}>选择已加载项</Checkbox>
    <span>已选 {selection.ids.length} 项</span>
    <Button disabled={disabled || !selection.ids.length} onClick={() => { setAll(false); setOpen(true); }}>批量生成所选</Button>
    <Button disabled={disabled} onClick={() => { setAll(true); setOpen(true); }}>预检当前范围全部</Button>
    {!!selection.ids.length && <Button type="text" onClick={() => selection.setIds([])}>清空选择</Button>}
    {open && <BatchCreatePanel scope={scope} sourceIds={all ? undefined : selection.ids} beforePreflight={beforePreflight} onClose={() => setOpen(false)}/>}
  </div>;
}

function BatchCreatePanel({ scope, sourceIds, beforePreflight, onClose }: { scope: BatchScope; sourceIds?: string[]; beforePreflight?: () => Promise<boolean>; onClose: () => void }) {
  const assets = !!scope.asset_kind;
  const [scene, setScene] = useState<BatchScene>(assets ? 'asset_image' : 'shot_image');
  const [config, setConfig] = useState<string>(), [count, setCount] = useState(1);
  const [mode, setMode] = useState<'missing' | 'regenerate'>('missing');
  const [aspect, setAspect] = useState<string>(), [resolution, setResolution] = useState('');
  const [preview, setPreview] = useState<BatchPreview | null>(null), [body, setBody] = useState<BatchRequest | null>(null);
  const [accepted, setAccepted] = useState<string[]>([]), [batch, setBatch] = useState<string>();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const lock = useRef(false), mounted = useRef(true);
  const request = useRef<AbortController | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
  const scopeKey = `batch:${JSON.stringify(scope)}:${scene}`;
  function invalidate() { setPreview(null); setBody(null); setError(''); }
  async function preflight() {
    if (lock.current || !config) return;
    lock.current = true; setBusy(true); setError('');
    try {
      if (beforePreflight && !await beforePreflight()) throw new Error('请先保存当前编辑，再预检批量生成。');
      const next: BatchRequest = { scene, scope, config_id: config, source_ids: sourceIds, mode, count: scene === 'shot_video' ? 1 : count,
        ...(assets ? { asset_parameters: { aspect, resolution: resolution.trim() || undefined } } : {}) };
      request.current = new AbortController();
      const result = await generationBatches.preflight(next, request.current.signal);
      if (mounted.current) { setBody(next); setPreview(result); setAccepted(result.items.filter(item => item.state === 'ready').map(item => item.source_id)); }
    } catch (cause) { if (mounted.current) { invalidate(); setError(errorMessage(cause)); } }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  async function submit() {
    if (!body || !preview || lock.current || !accepted.length) return;
    lock.current = true; setBusy(true); setError('');
    const value = { ...body, preflight_hash: preview.preflight_hash, accepted_ids: accepted };
    try {
      const key = await requestAttempt(scopeKey, value, attemptStorage());
      const created = await generationBatches.create(value, key);
      clearAttempt(scopeKey, attemptStorage());
      if (mounted.current) setBatch(created.id);
    } catch (cause) { if (mounted.current) setError(errorMessage(cause)); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  if (batch) return <BatchProgress id={batch} onClose={onClose}/>;
  return <Drawer open title="批量生成预检" width={800} onClose={() => !busy && onClose()} closable={!busy} maskClosable={!busy} keyboard={!busy}>
    <div className="batch-form">
      <p>{sourceIds ? `已选择 ${sourceIds.length} 项` : '服务端将读取当前分集或素材分类的全部匹配项'}。单批最多100项，生成后仍需审核采用。</p>
      {!assets && <label>生成类型<Select value={scene} disabled={busy} options={['shot_image', 'shot_video'].map(value => ({ value, label: labels[value] }))} onChange={value => { setScene(value); setConfig(undefined); invalidate(); }}/></label>}
      <label>生成模型<ConfigSelect key={scene} kind={scene === 'shot_video' ? 'video' : 'image'} autoDefault={false} value={config} disabled={busy} onChange={value => { setConfig(value); invalidate(); }}/></label>
      <Radio.Group value={mode} disabled={busy} onChange={event => { setMode(event.target.value); invalidate(); }}><Radio value="missing">仅生成缺失项</Radio><Radio value="regenerate">明确重新生成（可能再次计费）</Radio></Radio.Group>
      {scene !== 'shot_video' && <label>每项候选数量<Select value={count} disabled={busy} options={[1, 2, 3, 4].map(value => ({ value, label: `${value} 张` }))} onChange={value => { setCount(value); invalidate(); }}/></label>}
      {assets && <div className="batch-fields"><label>画幅<Select value={aspect} disabled={busy} allowClear placeholder="模型默认" options={['16:9', '9:16', '1:1', '4:3', '3:4'].map(value => ({ value, label: value }))} onChange={value => { setAspect(value); invalidate(); }}/></label><label>清晰度<Input value={resolution} maxLength={32} disabled={busy} placeholder="模型默认" onChange={event => { setResolution(event.target.value); invalidate(); }}/></label></div>}
      {!assets && <p>使用各分镜已保存的画幅、清晰度、布局及视频时长。预检会等待本页编辑保存。</p>}
      <Button loading={busy} disabled={!config} onClick={() => void preflight()}>检查可执行项</Button>
      {error && <Alert type="error" showIcon message={error} description="提交结果不明时保留当前预检直接重试，使用同一请求标识核对；来源冲突时请重新预检。"/>}
      {preview && <><Table size="small" rowKey="source_id" dataSource={preview.items} pagination={false} scroll={{ y: 320, x: 500 }}
        rowSelection={{ selectedRowKeys: accepted, onChange: keys => setAccepted(keys.map(String)), getCheckboxProps: item => ({ disabled: item.state !== 'ready' || busy }) }}
        columns={[{ title: '对象', dataIndex: 'name' }, { title: '状态', render: (_, item) => labels[item.state] ?? item.state },
          { title: '说明与参数', render: (_, item) => item.reason || Object.entries(item.parameters ?? {}).map(([key, value]) => `${({ aspect: '画幅', resolution: '清晰度', count: '数量', duration_ms: '时长(ms)' } as Record<string, string>)[key] ?? key}: ${value}`).join(' / ') }]}/>
        <p role="status">将创建 {accepted.length} 个任务，最多请求 {accepted.length * (scene === 'shot_video' ? 1 : count)} 个结果。此模型批次并发上限 {preview.concurrency}，不含手动单任务。</p>
        <Button type="primary" loading={busy} disabled={!accepted.length} onClick={() => void submit()}>确认并启动 {accepted.length} 项生成</Button></>}
    </div>
  </Drawer>;
}

export function BatchProgress({ id: initialId, onClose }: { id: string; onClose: () => void }) {
  const [id, setId] = useState(initialId);
  const [data, setData] = useState<BatchDetail | null>(null), [offset, setOffset] = useState(0), [revision, setRevision] = useState(0);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [task, setTask] = useState<string>();
  const [selected, setSelected] = useState<string[]>([]);
  const lock = useRef(false);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try { const next = await generationBatches.detail(id, offset, controller.signal); if (!controller.signal.aborted) { setData(next); setError(''); if (ongoing(next.status)) timer = setTimeout(load, 4000); } }
      catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)); }
    }
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [id, offset, revision]);
  async function control(action: 'pause' | 'resume' | 'cancel' | 'retry') {
    if (lock.current) return;
    if (action === 'cancel' && !await confirmAction('停止未执行项，并请求取消运行中的任务；已完成结果保留。确定取消？')) return;
    if (action === 'retry' && !await confirmAction(`重新生成所选 ${selected.length} 项可能再次计费，原结果保留。确定继续？`)) return;
    lock.current = true; setBusy(true); setError('');
    try {
      if (action === 'retry') { const scope = `batch-retry:${id}`; const key = await requestAttempt(scope, { item_ids: selected }, attemptStorage()); const next = await generationBatches.retry(id, selected, key); clearAttempt(scope, attemptStorage()); setSelected([]); setId(next.id); setOffset(0); setData(null); }
      else await generationBatches.control(id, action);
      setRevision(v => v + 1);
    } catch (cause) { setError(errorMessage(cause)); }
    finally { lock.current = false; setBusy(false); }
  }
  const scope = data?.scope.scope;
  return <Drawer open title="批量生成进度" width={880} onClose={() => !busy && onClose()} closable={!busy}>
    {error && <Alert type="error" message={error} action={<Button onClick={() => setRevision(v => v + 1)}>刷新</Button>}/>}
    {!data ? <Spin/> : <div className="batch-form"><h2>{labels[data.scene]} · {labels[data.status]}</h2>
      <p>离开页面后任务继续。生成结果不会自动采用。</p>
      <div className="batch-toolbar">{Object.entries(data.counts).map(([state, count]) => <Tag key={state}>{labels[state]} {count}</Tag>)}</div>
      <div className="batch-toolbar"><Button disabled={busy || data.status !== 'running'} onClick={() => void control('pause')}>暂停后续任务</Button><Button disabled={busy || !['paused', 'needs_review'].includes(data.status)} onClick={() => void control('resume')}>恢复批次</Button><Button danger disabled={busy || !ongoing(data.status)} onClick={() => void control('cancel')}>取消批次</Button><Button onClick={() => setRevision(v => v + 1)}>刷新进度</Button></div>
      {scope?.project_id && scope.episode_id && <Link to={episodePath(scope.project_id, scope.episode_id, data.scene === 'asset_image' ? 'assets' : 'storyboard')}>打开来源分集审核候选</Link>}
      {data.status === 'needs_review' && <Alert type="warning" message="有任务受理结果不明，后续派发已暂停。请进入任务详情核对，系统不会自动重发。"/>}
      <Table rowKey="id" dataSource={data.items} pagination={false} scroll={{ x: 620 }}
        rowSelection={{ selectedRowKeys: selected, onChange: keys => setSelected(keys.map(String)), preserveSelectedRowKeys: true, getCheckboxProps: item => ({ disabled: busy || !item.task.can_retry || item.task.can_resume }) }}
        columns={[{ title: '对象', dataIndex: 'name' }, { title: '状态', render: (_, item) => <span>{labels[item.status]}{item.error?.message || item.task.error?.message ? ` · ${item.error?.message || item.task.error?.message}` : ''}</span> }, { title: '操作', render: (_, item) => <Button onClick={() => setTask(item.task_id)}>{item.task.can_resume ? '安全恢复／详情' : '任务与结果'}</Button> }]}/>
      <Pagination current={offset / 20 + 1} pageSize={20} total={data.total} showSizeChanger={false} onChange={page => setOffset((page - 1) * 20)}/>
      <Button disabled={busy || !selected.length} onClick={() => void control('retry')}>重新生成所选失败项</Button>
    </div>}
    {task && <TaskDetail id={task} onClose={() => setTask(undefined)} onChanged={() => setRevision(v => v + 1)} onCreated={receipt => { setTask(receipt.generation_id); setRevision(v => v + 1); }}/>}
  </Drawer>;
}

export function BatchHistory({ selectedId, onSelect }: { selectedId?: string; onSelect: (id?: string) => void }) {
  const selection = useBatchSelection('history');
  const [open, setOpen] = useState(false), [items, setItems] = useState<Batch[]>([]), [total, setTotal] = useState(0), [offset, setOffset] = useState(0);
  const [error, setError] = useState(''), [revision, setRevision] = useState(0);
  useEffect(() => { if (!open) return; const controller = new AbortController(); void generationBatches.list(offset, controller.signal).then(page => { if (controller.signal.aborted) return; setItems(page.items); setTotal(page.total); setError(''); }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); }); return () => controller.abort(); }, [open, offset, revision]);
  if (!selection.enabled && !selectedId) return null;
  return <><Button onClick={() => { setOpen(true); setRevision(v => v + 1); }}>批次记录</Button>
    <Drawer open={open} title="批次记录" width={680} onClose={() => setOpen(false)}>
      {error && <Alert type="error" message={error}/>}
      <Button onClick={() => setRevision(v => v + 1)}>刷新记录</Button>
      <Table rowKey="id" dataSource={items} pagination={false} columns={[{ title: '类型', render: (_, item) => labels[item.scene] }, { title: '状态', render: (_, item) => labels[item.status] }, { title: '数量', dataIndex: 'total' }, { title: '操作', render: (_, item) => <Button onClick={() => { onSelect(item.id); setOpen(false); }}>查看批次</Button> }]}/>
      <Pagination current={offset / 20 + 1} pageSize={20} total={total} showSizeChanger={false} onChange={page => setOffset((page - 1) * 20)}/>
    </Drawer>{selectedId && <BatchProgress key={selectedId} id={selectedId} onClose={() => onSelect(undefined)}/>}</>;
}
