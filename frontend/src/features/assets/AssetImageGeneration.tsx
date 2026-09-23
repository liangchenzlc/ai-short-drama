import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Input, InputNumber, Select, Tag } from 'antd';
import type { LibraryAssetRead, AssetScope } from '../../api/modules/assets';
import type { GenerationSummary, ImageGenerationRequest } from '../../api/types/generations';
import { ConfigSelect } from '../generations/ConfigSelect';
import { dateLabel, statusLabels, taskLabel } from '../generations/presentation';
import { useAssetImageGeneration } from './useAssetImageGeneration';
import { assetImageGenerationBlockReason } from './asset-image-generation';

const aspectOptions = ['16:9', '9:16', '1:1', '4:3', '3:4'].map((value) => ({ value, label: value }));
const active = (task: GenerationSummary) => task.status === 'queued' || task.status === 'running';

export function AssetImageGeneration({
  asset, scope, readOnly, onSaveBeforeGenerate, onCandidatesChanged, onSubmissionBusyChange,
}: {
  asset: LibraryAssetRead;
  scope?: AssetScope;
  readOnly: boolean;
  onSaveBeforeGenerate: () => Promise<LibraryAssetRead | null>;
  onCandidatesChanged: () => void;
  onSubmissionBusyChange: (busy: boolean) => void;
}) {
  const [configId, setConfigId] = useState<string>();
  const [count, setCount] = useState(1);
  const [aspect, setAspect] = useState<ImageGenerationRequest['parameters']['aspect']>();
  const [resolution, setResolution] = useState('');
  const { tasks, hasMore, refreshError, submitError, submitting, actionTaskId, submit, perform, load, loadMore } =
    useAssetImageGeneration(asset, onSaveBeforeGenerate, onCandidatesChanged, onSubmissionBusyChange);
  const blockReason = assetImageGenerationBlockReason(asset, configId);

  return <div className="asset-image-generation">
    {!readOnly && <div className="asset-image-generation-form">
      <label>生图模型<ConfigSelect kind="image" value={configId} onChange={setConfigId} onResolvedChange={setConfigId} disabled={submitting} label="生图模型"/></label>
      <label>候选数量<InputNumber min={1} max={4} value={count} disabled={submitting} onChange={(value) => setCount(value ?? 1)}/></label>
      <label>画面比例<Select allowClear value={aspect} options={aspectOptions} disabled={submitting} placeholder="模型默认" onChange={setAspect}/></label>
      <label>分辨率<Input allowClear value={resolution} disabled={submitting} placeholder="模型默认，如 1024x1024" onChange={(event) => setResolution(event.target.value)}/></label>
      <div className="asset-generation-submit">
        <Button type="primary" loading={submitting} disabled={submitting || !!blockReason} onClick={() => void submit({ configId, supplement: '', count, aspect, resolution, scope })}>保存并生成图片</Button>
        <span>{blockReason ?? '生成任务不会自动采用图片；请在候选区明确确认。'}</span>
      </div>
    </div>}
    {(submitError || refreshError) && <Alert type="warning" showIcon message={submitError || refreshError}/>}
    <div className="asset-generation-history-heading">
      <h4>生成记录</h4>
      <Button size="small" disabled={submitting} onClick={() => void load()}>刷新</Button>
    </div>
    {!tasks.length ? <p className="asset-generation-empty">暂无生成记录。</p> : <div className="asset-generation-tasks">
      {tasks.map((task) => {
        return <article key={task.generation_id} className="asset-generation-task">
          <div><Tag className={'generation-status status-' + task.status}>{taskLabel(task)}</Tag><span>{dateLabel(task.created_at)}</span><small>任务 {task.generation_id}</small></div>
          {task.error && <p className="asset-generation-task-error">{task.error.message}</p>}
          <div className="asset-generation-task-actions">
            <Link to={`/tasks/image?task=${task.generation_id}`}>查看任务详情</Link>
            {task.can_cancel && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={() => window.confirm('模型调用可能已经发生。仍要请求取消任务吗？') && void perform(task, 'cancel')}>请求取消</Button>}
            {task.can_resume && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={() => window.confirm('恢复会继续查询或保存既有调用，不会盲目重新生成。确定恢复吗？') && void perform(task, 'resume')}>安全恢复</Button>}
            {task.can_retry && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={() => window.confirm('将按原任务冻结的旧输入重新生成，不会读取当前素材编辑，且可能再次产生费用。确定继续吗？') && void perform(task, 'retry')}>按旧输入重新生成</Button>}
            {active(task) && <span>{statusLabels[task.status]}</span>}
          </div>
        </article>;
      })}
    </div>}
    {hasMore && <Button disabled={submitting || !!actionTaskId} onClick={() => void loadMore()}>加载更多生成记录</Button>}
  </div>;
}