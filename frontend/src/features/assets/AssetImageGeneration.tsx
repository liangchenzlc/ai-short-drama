import { useState } from 'react';
import { Alert, Button, Input, InputNumber, Select, Tag } from 'antd';
import type { LibraryAssetRead } from '../../api/modules/assets';
import type { GenerationSummary, ImageGenerationRequest } from '../../api/types/generations';
import { ConfigSelect } from '../generations/ConfigSelect';
import { dateLabel, statusLabels, taskLabel } from '../generations/presentation';
import { useAssetImageGeneration } from './useAssetImageGeneration';
import { assetImageGenerationBlockReason } from './asset-image-generation';

const aspectOptions = ['16:9', '9:16', '1:1', '4:3', '3:4'].map((value) => ({ value, label: value }));
const active = (task: GenerationSummary) => task.status === 'queued' || task.status === 'running';

export function AssetImageGeneration({
  asset, readOnly, onSaveBeforeGenerate, onCandidatesChanged, onSubmissionBusyChange,
}: {
  asset: LibraryAssetRead;
  readOnly: boolean;
  onSaveBeforeGenerate: () => Promise<LibraryAssetRead | null>;
  onCandidatesChanged: () => void;
  onSubmissionBusyChange: (busy: boolean) => void;
}) {
  const [configId, setConfigId] = useState<string>();
  const [supplement, setSupplement] = useState('');
  const [count, setCount] = useState(1);
  const [aspect, setAspect] = useState<ImageGenerationRequest['parameters']['aspect']>();
  const [resolution, setResolution] = useState('');
  const { tasks, details, hasMore, refreshError, submitError, submitting, actionTaskId, submit, perform, load, loadMore, loadDetail } =
    useAssetImageGeneration(asset, onSaveBeforeGenerate, onCandidatesChanged, onSubmissionBusyChange);
  const blockReason = assetImageGenerationBlockReason(asset, configId);

  return <div className="asset-image-generation">
    {!readOnly && <div className="asset-image-generation-form">
      <label>生图模型<ConfigSelect kind="image" value={configId} onChange={setConfigId} onResolvedChange={setConfigId} disabled={submitting} label="生图模型"/></label>
      <label className="asset-generation-supplement">本次补充要求
        <Input.TextArea rows={3} maxLength={4000} value={supplement} disabled={submitting} placeholder="可选，只描述本次希望补充的画面要求" onChange={(event) => setSupplement(event.target.value)}/>
      </label>
      <label>候选数量<InputNumber min={1} max={4} value={count} disabled={submitting} onChange={(value) => setCount(value ?? 1)}/></label>
      <label>画面比例<Select allowClear value={aspect} options={aspectOptions} disabled={submitting} placeholder="模型默认" onChange={setAspect}/></label>
      <label>分辨率<Input allowClear value={resolution} disabled={submitting} placeholder="模型默认，如 1024x1024" onChange={(event) => setResolution(event.target.value)}/></label>
      <div className="asset-generation-submit">
        <Button type="primary" loading={submitting} disabled={submitting || !!blockReason} onClick={() => void submit({ configId, supplement, count, aspect, resolution })}>保存并生成图片</Button>
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
        const detail = details[task.generation_id];
        return <article key={task.generation_id} className="asset-generation-task">
          <div><Tag className={'generation-status status-' + task.status}>{taskLabel(task)}</Tag><span>{dateLabel(task.created_at)}</span><small>任务 {task.generation_id}</small></div>
          {task.error && <p className="asset-generation-task-error">{task.error.message}</p>}
          {detail?.result.partial && <p>部分结果已保存，可先在候选区查看。</p>}
          {!!detail?.result.warnings?.length && <Alert type="warning" showIcon message={detail.result.warnings.map((warning) => warning.message).join('；')}/>}
          <div className="asset-generation-task-actions">
            {!detail && <Button type="link" onClick={() => void loadDetail(task.generation_id)}>查看详情</Button>}
            {detail && <details className="asset-generation-detail"><summary>输入快照与实际提示词</summary>
              {detail.effective_prompt && <pre className="generation-json">{detail.effective_prompt}</pre>}
              {detail.source_snapshot && <pre className="generation-json">{JSON.stringify(detail.source_snapshot, null, 2)}</pre>}
            </details>}
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