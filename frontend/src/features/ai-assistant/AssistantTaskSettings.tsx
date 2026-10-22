import { useEffect, useState, type ReactNode } from 'react';
import { Button, InputNumber, Select } from 'antd';
import { projectsApi, type RemoteProject } from '../../api/modules/projects';
import { errorMessage } from '../../api/http';
import type { TaskExecutionTarget, TaskMediaLimits } from '../../api/types/assistant';
import type { CanvasSummaryDto } from '../../api/types/canvases';
import { StudioFeedback } from '../../components/ui/StudioFeedback';

export function AssistantTaskSettings({ target, memoryProjects, limits, currentTarget, disabled, onTarget, onMemory, onLimits, children }: {
  target: TaskExecutionTarget | null; memoryProjects: string[]; limits: TaskMediaLimits;
  currentTarget?: TaskExecutionTarget; disabled: boolean; children?: ReactNode;
  onTarget: (value: TaskExecutionTarget | null) => void;
  onMemory: (value: string[]) => void; onLimits: (value: TaskMediaLimits) => void;
}) {
  const [projectId, setProjectId] = useState(target?.project_id ?? '');
  const [known, setKnown] = useState<Record<string, RemoteProject>>({});
  const [items, setItems] = useState<RemoteProject[]>([]);
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [canvases, setCanvases] = useState<CanvasSummaryDto[]>([]);
  const [canvasLoading, setCanvasLoading] = useState(false);
  const [canvasError, setCanvasError] = useState('');
  const mode = known[projectId]?.workspaceMode ?? (target?.project_id === projectId ? target.mode : undefined);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    const timer = setTimeout(() => projectsApi.list(offset, query, controller.signal)
      .then(page => { if (!controller.signal.aborted) {
        setItems(page.items); setTotal(page.total);
        setKnown(old => ({ ...old, ...Object.fromEntries(page.items.map(item => [item.projectId, item])) }));
      } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); }), 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, offset, revision]);
  const selectedIds = [...new Set([target?.project_id, ...memoryProjects].filter((id): id is string => !!id))].join(',');
  useEffect(() => {
    const controller = new AbortController();
    const missing = selectedIds.split(',').filter(id => id && !known[id]);
    void Promise.allSettled(missing.map(id => projectsApi.get(id, controller.signal))).then(results => {
      if (controller.signal.aborted) return;
      const found = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []);
      setKnown(old => ({ ...old, ...Object.fromEntries(found.map(item => [item.projectId, item])) }));
      const failed = results.find(result => result.status === 'rejected');
      if (failed?.status === 'rejected') setError(`部分已选作品不可读取，请核对目标和记忆范围：${errorMessage(failed.reason)}`);
    });
    return () => controller.abort();
  }, [selectedIds, revision]);
  useEffect(() => {
    setCanvases([]); setCanvasError('');
    if (mode !== 'infinite_canvas') { setCanvasLoading(false); return; }
    const controller = new AbortController(); setCanvasLoading(true);
    projectsApi.canvases(projectId, controller.signal)
      .then(next => { if (!controller.signal.aborted) setCanvases(next); })
      .catch(cause => { if (!controller.signal.aborted) setCanvasError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setCanvasLoading(false); });
    return () => controller.abort();
  }, [mode, projectId, revision]);
  const options = [...new Set([...items.map(item => item.projectId), projectId, ...memoryProjects].filter(Boolean))]
    .map(id => ({ value: id, label: known[id]?.name ?? `作品 ${id}` }));
  const chooseProject = (id: string) => {
    setProjectId(id);
    onTarget(id && known[id]?.workspaceMode === 'standard' ? { mode: 'standard', project_id: id } : null);
  };
  return <div className="agent-context-dialog-body ai-assistant-task-settings">
    <label>工具写入目标<Select aria-label="工具写入目标" showSearch allowClear filterOption={false}
      value={projectId || undefined} placeholder="不指定写入目标" loading={loading} disabled={disabled || loading}
      options={options} onSearch={value => { setQuery(value); setOffset(0); }} onChange={id => chooseProject(id ?? '')}/></label>
    {currentTarget && <Button size="small" disabled={disabled} onClick={() => { setProjectId(currentTarget.project_id); onTarget(currentTarget); }}>使用当前页面目标</Button>}
    {mode === 'infinite_canvas' && <label>写入画布<Select aria-label="写入画布" value={target?.project_id === projectId ? target.canvas_id : undefined}
      disabled={disabled || canvasLoading} loading={canvasLoading} placeholder={canvasLoading ? '正在读取画布' : '明确选择写入画布'}
      options={canvases.map(canvas => ({ value: canvas.source_key, label: canvas.title || canvas.source_key }))}
      onChange={id => onTarget({ mode: 'infinite_canvas', project_id: projectId, canvas_id: id })}/></label>}
    {mode === 'infinite_canvas' && !canvasLoading && !canvasError && !canvases.length && <p className="ai-assistant-meta">此作品没有可写入画布，请选择其他目标。</p>}
    {canvasError && <StudioFeedback type="error" title="读取写入画布" message={canvasError} noticeKey="assistant-target-canvas" actions={<Button size="small" onClick={() => setRevision(old => old + 1)}>重新读取画布</Button>}/>}
    <label>作品长期记忆<Select aria-label="作品长期记忆" mode="multiple" showSearch allowClear filterOption={false}
      value={memoryProjects} placeholder="仅账号和当前会话记忆" loading={loading} disabled={disabled}
      maxTagCount="responsive" options={options.map(item => ({ ...item, disabled: memoryProjects.length >= 16 && !memoryProjects.includes(item.value) }))}
      onSearch={value => { setQuery(value); setOffset(0); }} onChange={onMemory}/></label>
    {error && <StudioFeedback type="error" title="读取作品目录" message={error} noticeKey="assistant-target-projects" actions={<Button size="small" onClick={() => setRevision(old => old + 1)}>重新读取作品</Button>}/>}
    {total > 20 && <div className="ai-assistant-history-footer"><Button size="small" disabled={disabled || loading || !offset} onClick={() => setOffset(old => Math.max(0, old - 20))}>上一页作品</Button><span>{offset + 1}–{Math.min(total, offset + 20)} / {total}</span><Button size="small" disabled={disabled || loading || offset + 20 >= total} onClick={() => setOffset(old => old + 20)}>下一页作品</Button></div>}
    <p className="ai-assistant-meta">参考主体、作品记忆与写入目标分别设置。选择画布不会读取整图正文；设置随本条消息发送，在工具边界生效。</p>
    <div className="ai-assistant-media-limits">{(['images', 'videos', 'audio'] as const).map((kind, index) => <label key={kind}>{['图片上限', '视频上限', '音频上限'][index]}<InputNumber aria-label={['图片上限', '视频上限', '音频上限'][index]} min={0} max={1000} precision={0} placeholder="不限数量" disabled={disabled} value={limits[kind]} onChange={value => onLimits({ ...limits, [kind]: value })}/></label>)}</div>
    {children}
  </div>;
}
