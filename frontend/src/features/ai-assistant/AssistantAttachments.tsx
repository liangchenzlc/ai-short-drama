import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Button, Spin } from 'antd';
import { X } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { ApiError, errorMessage } from '../../api/http';
import type { AssistantAttachmentDraft, AssistantAttachmentMetadata, AssistantAttachmentSelection, AssistantTask } from '../../api/types/assistant';
import { StudioFeedback } from '../../components/ui/StudioFeedback';

export interface AssistantAttachmentsController { prepare: () => Promise<AssistantAttachmentSelection | null> }
export const AssistantAttachments = forwardRef<AssistantAttachmentsController, {
  task: AssistantTask | null; value: AssistantAttachmentDraft | null;
  disabled: boolean; onChange: (value: AssistantAttachmentDraft | null) => void;
}>(function AssistantAttachments({ task, value, disabled, onChange }, ref) {
  const [metadata, setMetadata] = useState<AssistantAttachmentMetadata | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const update = () => setRevision(old => old + 1);
    window.addEventListener('focus', update);
    return () => window.removeEventListener('focus', update);
  }, []);
  useEffect(() => {
    if (!task) { setMetadata(null); setError(''); setLoading(false); return; }
    const controller = new AbortController(); setLoading(true); setError('');
    assistantApi.taskAttachments(task.id, controller.signal)
      .then(next => { if (!controller.signal.aborted) setMetadata(next); })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [task?.id, task?.input_revision, task?.applied_input_revision, revision]);
  const current = metadata?.task_id === task?.id ? metadata : null;
  const stale = !!value && (value.taskId !== task?.id || !!current && value.expectedInputRevision !== current.input_revision);
  const items = current?.items ?? [];
  useImperativeHandle(ref, () => ({
    async prepare() {
      if (!value) return null;
      if (!task || stale) throw new ApiError('任务附件已变化，请先核对附件选择。', 'assistant_selection_changed');
      const latest = await assistantApi.taskAttachments(task.id);
      if (latest.input_revision !== value.expectedInputRevision) {
        setMetadata(latest);
        throw new ApiError('任务收到其他输入，附件选择已保留，请核对后发送。', 'assistant_selection_changed');
      }
      return { expected_input_revision: value.expectedInputRevision, retain_ids: value.retainIds };
    },
  }));
  if (!task && !value) return null;
  return <>
    <div className="ai-assistant-chips" aria-label="当前任务附件" aria-busy={loading}>
      {loading ? <Spin size="small"/> : null}
      {items.map(item => <span key={item.id} className="ai-assistant-chip" title={item.name}>
        <span>{item.name}{value && !value.retainIds.includes(item.id) ? ' · 待移除' : !item.available ? ' · 不可用' : ''}</span>
        <button type="button" aria-label={`${value && !value.retainIds.includes(item.id) ? '保留' : '移除'}任务附件${item.name}`} disabled={disabled || loading || stale} onClick={() => {
          if (!current || !task) return;
          const next = value ?? { taskId: task.id, expectedInputRevision: current.input_revision, retainIds: items.map(item => item.id) };
          onChange({ ...next, retainIds: next.retainIds.includes(item.id) ? next.retainIds.filter(id => id !== item.id) : [...next.retainIds, item.id] });
        }}><X size={12}/></button>
      </span>)}
      {value && !stale && <span className="ai-assistant-meta">附件变更随下一条消息发送</span>}
      {current?.pending && <span className="ai-assistant-meta">{task?.status === 'stopped' ? '附件变更在继续后生效' : '附件变更等待工具边界生效'}</span>}
    </div>
    {error && <StudioFeedback type="error" title="读取任务附件" message={error} noticeKey={`assistant-task-attachments:${task?.id}`} actions={<Button size="small" onClick={() => setRevision(old => old + 1)}>重新读取附件</Button>}/>}
    {stale && <StudioFeedback type="warning" persistent title="附件选择需要核对" message="任务已变化，原附件选择已保留。请采用当前任务附件后重新编辑。" noticeKey="assistant-task-attachments-stale" actions={<Button size="small" disabled={disabled || loading} onClick={() => { onChange(null); setRevision(old => old + 1); }}>采用当前任务附件</Button>}/>}
  </>;
});
