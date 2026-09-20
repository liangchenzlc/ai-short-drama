import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Form, Input, Pagination, Select, Table, Tabs, Tag } from 'antd';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { generations } from '../../api/modules/generations';
import type { GenerationFilters, GenerationKind, GenerationReceipt, GenerationSummary } from '../../api/types/generations';
import { ConfigSelect } from '../../features/generations/ConfigSelect';
import { CreateGeneration } from '../../features/generations/CreateGeneration';
import { TaskDetail } from '../../features/generations/TaskDetail';
import { dateLabel, kindLabels, statusLabels, taskLabel } from '../../features/generations/presentation';
import { isServerId } from '../../features/generations/attempt';
import { useRemotePage } from '../../features/generations/useRemotePage';

const activeTasks = (items: GenerationSummary[]) => items.some((item) => item.status === 'queued' || item.status === 'running');
const filterNames = ['status', 'config_id', 'source_id', 'created_after', 'created_before'] as const;
export function TasksPage({ kind }: { kind: GenerationKind }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [form] = Form.useForm();
  const [creating, setCreating] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(() => !window.matchMedia('(max-width: 760px)').matches);
  const filterValue = (name: string) => name === 'status' && params.get(name) === 'unknown' ? 'failed' : params.get(name);
  const filterKey = JSON.stringify(Object.fromEntries(filterNames.map((name) => [name, filterValue(name) ?? ''])));
  useEffect(() => { form.setFieldsValue(JSON.parse(filterKey)); }, [form, filterKey]);
  const rawOffset = Number(params.get('offset') ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const query: GenerationFilters = { service_type: kind, offset, limit: 20 };
  for (const name of filterNames) { const value = filterValue(name); if (value) query[name] = name.startsWith('created_') && !Number.isNaN(new Date(value).getTime()) ? new Date(value).toISOString() : value; }
  if (query.source_id) query.source_scene = 'shot_image';
  const { data, error, loading, refresh } = useRemotePage(query, generations.list, activeTasks);
  const selected = params.get('task');
  function selectTask(id?: string) { const next = new URLSearchParams(params); if (id) next.set('task', id); else next.delete('task'); setParams(next); }
  function created(receipt: GenerationReceipt) { setCreating(false); selectTask(receipt.generation_id); refresh(); }
  function filter(values: Record<string, string | undefined>) {
    const next = new URLSearchParams();
    for (const name of filterNames) if (values[name]?.trim()) next.set(name, values[name]!.trim());
    setParams(next);
  }
  return <section className="studio-page generation-page" aria-labelledby="tasks-title">
    <div className="studio-page-head"><div><h1 id="tasks-title">任务管理</h1><p>提交创作内容，追踪生成进度，查看每次调用与保存结果。</p></div><Button type="primary" onClick={() => setCreating(true)}>新建{kindLabels[kind]}任务</Button></div>
    <Tabs activeKey={kind} onChange={(value) => navigate(`/tasks/${value}`)} items={Object.entries(kindLabels).map(([key, label]) => ({ key, label: `${label}任务` }))} />
    <details className="generation-filter-panel" open={filtersOpen} onToggle={(event) => setFiltersOpen(event.currentTarget.open)}><summary>筛选任务<span>状态 / 模型 / 来源 / 时间</span></summary>
    <Form form={form} layout="vertical" className="generation-filters" initialValues={Object.fromEntries(filterNames.map((name) => [name, params.get(name) ?? undefined]))} onFinish={filter}>
      <Form.Item name="status" label="状态"><Select allowClear placeholder="全部状态" options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))} /></Form.Item>
      <Form.Item name="config_id" label="模型配置"><ConfigSelect kind={kind} allowDefault={false} /></Form.Item>
      <Form.Item name="source_id" label="来源分镜 ID" rules={[{ validator: (_, value?: string) => !value || isServerId(value.trim()) ? Promise.resolve() : Promise.reject(new Error('请输入真实服务端 ID')) }]}><Input placeholder="真实服务端 ID" /></Form.Item>
      <Form.Item name="created_after" label="创建时间起"><Input type="datetime-local" /></Form.Item>
      <Form.Item name="created_before" label="创建时间止"><Input type="datetime-local" /></Form.Item>
      <div className="generation-filter-actions"><Button htmlType="submit" type="primary">筛选</Button><Button onClick={() => { form.resetFields(); form.setFieldsValue(Object.fromEntries(filterNames.map((name) => [name, undefined]))); setParams({}); }}>重置</Button></div>
    </Form></details>
    <div className="generation-list-toolbar"><p>{data ? `共 ${data.total} 个任务` : '生成历史'}<span>活跃任务自动更新</span></p><Button onClick={refresh} loading={loading}>刷新</Button></div>
    {error && <Alert type="error" showIcon message={error} description={data ? '保留上次查询结果，请刷新核对最新状态。' : undefined} action={<Button onClick={refresh}>重新加载</Button>} />}
    <Table<GenerationSummary> rowKey="generation_id" dataSource={data?.items ?? []} loading={loading} pagination={false}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={error ? '暂时无法加载任务' : '还没有符合条件的任务'} /> }}
      columns={[
        { title: '任务 / 模型', key: 'task', render: (_, item) => <div className="generation-task-cell"><button className="generation-link" onClick={() => selectTask(item.generation_id)}>{item.config?.name ?? `${kindLabels[kind]}生成`}</button><span>{item.config?.model_key ?? item.generation_id}</span></div> },
        { title: '状态', key: 'status', width: 115, render: (_, item) => <Tag className={`generation-status status-${item.status}`}>{taskLabel(item)}</Tag> },
        { title: '创建时间', dataIndex: 'created_at', width: 185, responsive: ['md'], render: (value: string) => dateLabel(value) },
        { title: '操作', key: 'action', width: 90, render: (_, item) => <Button onClick={() => selectTask(item.generation_id)}>详情</Button> },
      ]} />
    <Pagination className="generation-pagination" current={Math.floor(offset / 20) + 1} pageSize={20} total={data?.total ?? 0} showSizeChanger={false} hideOnSinglePage={false} onChange={(page) => { const next = new URLSearchParams(params); next.set('offset', String((page - 1) * 20)); setParams(next); }} />
    {creating && <CreateGeneration kind={kind} onClose={() => setCreating(false)} onCreated={created} />}
    {selected && !creating && <TaskDetail key={selected} id={selected} onClose={() => selectTask()} onChanged={refresh} onCreated={created} />}
  </section>;
}
