import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Pagination, Table, Tabs, Tag } from 'antd';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { generations } from '../../api/modules/generations';
import { assetLibraries } from '../../api/modules/assets';
import type { GenerationFilters, GenerationKind, GenerationReceipt, GenerationSummary } from '../../api/types/generations';
import { episodePath, projectPath } from '../../app/paths';
import { Icon } from '../../components/ui/Icon';
import { ListToolbar, PageHeader } from '../../components/ui/Workspace';
import { BatchHistory } from '../../features/generations/BatchGeneration';
import { dateLabel, generationError, kindLabels, statusLabels, taskLabel } from '../../features/generations/presentation';
import { taskOrigin } from '../../features/generations/task-content';
import { taskSourceTarget } from '../../features/generations/task-source';
import { useRemotePage } from '../../features/generations/useRemotePage';
import './tasks.css';

const CreateGeneration = lazy(() => import('../../features/generations/CreateGeneration').then(module => ({ default: module.CreateGeneration })));
const TaskDetail = lazy(() => import('../../features/generations/TaskDetail').then(module => ({ default: module.TaskDetail })));
const activeTasks = (items: GenerationSummary[]) => items.some(item => item.status === 'queued' || item.status === 'running');

function TaskResultAction({ task, onOpen }: { task: GenerationSummary; onOpen: (id: string) => void }) {
  const navigate = useNavigate();
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const target = taskSourceTarget(task);
  async function openPersonalAsset(assetId: string) {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller;
    setOpening(true); setError('');
    try {
      const asset = await assetLibraries.detail(assetId, controller.signal);
      if (!controller.signal.aborted) navigate(`/assets/${asset.kind}`);
    } catch (cause) {
      if (!controller.signal.aborted) setError(generationError(cause));
    } finally {
      if (!controller.signal.aborted) { request.current = null; setOpening(false); }
    }
  }
  if (task.status === 'failed') return <div className="task-result-actions">
    <p className="task-failure-reason">{task.error?.message || '任务未完成，服务端未提供失败原因。'}</p>
    {task.can_resume || task.can_retry ? <Button onClick={() => onOpen(task.generation_id)}>
      {task.can_resume ? '安全恢复' : '重新生成'}
    </Button> : null}
  </div>;
  if (task.status !== 'succeeded') return task.can_cancel
    ? <Button onClick={() => onOpen(task.generation_id)}>请求取消</Button> : null;
  if (target?.kind === 'episode') return <Link className="task-source-link"
    to={episodePath(target.project_id, target.episode_id, target.stage)}>打开来源</Link>;
  if (target?.kind === 'project') return <Link className="task-source-link"
    to={`${projectPath(target.project_id)}?section=resources`}>打开来源</Link>;
  if (target?.kind === 'asset') return <div className="task-result-actions">
    <Button loading={opening} onClick={() => void openPersonalAsset(target.asset_id)}>打开来源</Button>
    {error && <span className="task-failure-reason" role="alert">{error}</span>}
  </div>;
  if (task.service_type === 'image' || task.service_type === 'video') return <Link className="task-source-link"
    to={`/media-library/${task.service_type}`}>查看作品</Link>;
  return <Button onClick={() => onOpen(task.generation_id)}>查看结果</Button>;
}

export function TasksPage({ kind }: { kind: GenerationKind }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const rawStatus = params.get('status') === 'unknown' ? 'failed' : params.get('status');
  const status = rawStatus && Object.hasOwn(statusLabels, rawStatus) ? rawStatus : undefined;
  const rawOffset = Number(params.get('offset') ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const query: GenerationFilters = { service_type: kind, offset, limit: 20, ...(status ? { status } : {}) };
  const { data, error, loading, refresh } = useRemotePage(query, generations.list, activeTasks);
  const selected = params.get('task');
  function selectTask(id?: string) {
    const next = new URLSearchParams(params);
    if (id) next.set('task', id); else next.delete('task');
    setParams(next);
  }
  function created(receipt: GenerationReceipt) {
    setCreating(false); selectTask(receipt.generation_id); refresh();
  }
  return <section className="studio-page generation-page tasks-page" aria-labelledby="tasks-title">
    <PageHeader id="tasks-title" title="任务管理" description="查看生成进度，处理失败任务，找到已完成的作品。" actions={<>
      <BatchHistory selectedId={params.get('batch') ?? undefined} onSelect={id => {
        const next = new URLSearchParams(params);
        if (id) next.set('batch', id); else next.delete('batch'); setParams(next);
      }}/>
      <Button type="primary" icon={<Icon name="plus" size={16}/>} onClick={() => setCreating(true)}>新建{kindLabels[kind]}任务</Button>
    </>} />
    <Tabs activeKey={kind} onChange={value => navigate(`/tasks/${value}`)}
      items={Object.entries(kindLabels).map(([key, label]) => ({ key, label: `${label}任务` }))} />
    <nav className="task-status-filters" aria-label="按任务状态筛选">
      { [['', '全部'], ...Object.entries(statusLabels)].map(([value, label]) => <button key={value} type="button"
        aria-pressed={(status || '') === value} onClick={() => {
          const next = new URLSearchParams(params);
          if (value) next.set('status', value); else next.delete('status');
          next.delete('offset'); setParams(next);
        }}>{label}</button>)}
    </nav>
    <ListToolbar count={data ? `共 ${data.total} 个任务` : '生成历史'}
      hint={data && activeTasks(data.items) ? '本页有任务处理中，状态自动更新' : '按创建时间查看生成记录'} actions={null} />
    {error && <Alert type="error" showIcon message={error}
      description={data ? '保留上次查询结果，重新加载后核对最新状态。' : undefined}
      action={<Button onClick={refresh} loading={loading}>重新加载</Button>} />}
    <Table<GenerationSummary> rowKey="generation_id" dataSource={data?.items ?? []} loading={loading} pagination={false}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={error ? '暂时无法加载任务' : status ? '没有该状态的任务' : `开始第一次${kindLabels[kind]}生成`} /> }}
      scroll={{ x: 1120 }} columns={[
        { title: '任务 ID', key: 'id', width: 170, render: (_, item) => <span className="generation-id">{item.generation_id}</span> },
        { title: '项目 / 集 / 主题', key: 'origin', width: 260, render: (_, item) => <span>{taskOrigin(item)}</span> },
        { title: '任务 / 模型', key: 'task', width: 225, render: (_, item) => <div className="generation-task-cell">
          <strong>{item.source?.scene === 'script_assets' ? '剧本素材提取' : item.config?.name ?? `${kindLabels[kind]}生成`}</strong>
          <span>{item.source?.scene === 'script_assets' ? item.config?.name : item.config?.model_key ?? item.generation_id}</span>
        </div> },
        { title: '状态', key: 'status', width: 105, render: (_, item) => <Tag className={`generation-status status-${item.status}`}>{taskLabel(item)}</Tag> },
        { title: '创建时间', dataIndex: 'created_at', width: 175, responsive: ['md'], render: (value: string) => dateLabel(value) },
        { title: '结果', key: 'action', width: 240, fixed: 'right', render: (_, item) => <TaskResultAction task={item} onOpen={selectTask}/> },
      ]} />
    <Pagination className="generation-pagination" current={Math.floor(offset / 20) + 1} pageSize={20}
      total={data?.total ?? 0} showSizeChanger={false} hideOnSinglePage onChange={page => {
        const next = new URLSearchParams(params);
        next.set('offset', String((page - 1) * 20)); setParams(next);
      }} />
    <Suspense fallback={<p role="status" className="generation-hint">正在加载任务工作区…</p>}>
      {creating && <CreateGeneration kind={kind} onClose={() => setCreating(false)} onCreated={created} />}
      {selected && !creating && <TaskDetail key={selected} id={selected} onClose={() => selectTask()} onChanged={refresh} onCreated={created} />}
    </Suspense>
  </section>;
}
