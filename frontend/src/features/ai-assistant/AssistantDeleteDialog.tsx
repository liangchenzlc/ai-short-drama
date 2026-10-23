import { Button } from 'antd';
import type { AssistantDeletionInput, GlobalAssistantConversation } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';

export function AssistantDeleteDialog({ conversation, busy, pending, error, layout, onClose, onSubmit, onRetry }: {
  conversation: GlobalAssistantConversation; busy: boolean; pending: boolean;
  error: string; layout?: 'standard'; onClose: () => void;
  onSubmit: (body: AssistantDeletionInput) => void; onRetry: () => void;
}) {
  return <Dialog title="删除对话" className={`ai-assistant-delete-dialog${layout === 'standard' ? ' standard-creation-dialog standard-assistant-dialog' : ''}`} canClose={!busy} onClose={onClose}>
    <div className="ai-assistant-delete-body">
      <p>永久删除“{conversation.title}”？删除后无法恢复。</p>
      <p className="ai-assistant-meta">当前操作结束后停止并删除对话。已创建的素材、分镜、画布节点和媒体成果会保留。</p>
      {error && <StudioFeedback scope="chat" type="error" title="删除对话" message={error} noticeKey={`assistant-delete:${conversation.id}`}/>}
      {pending && <StudioFeedback scope="chat" type="warning" persistent title="删除结果待核对" message="原删除请求已保留，请使用同一请求核对。" noticeKey={`assistant-delete-receipt:${conversation.id}`} actions={<Button size="small" disabled={busy} onClick={onRetry}>使用原请求核对删除</Button>}/>}
    </div>
    <footer className="ai-assistant-history-footer"><Button disabled={busy} onClick={onClose}>取消</Button><Button danger type="primary" aria-label="永久删除" aria-busy={busy} loading={busy} disabled={busy || pending} onClick={() => onSubmit({ row_version: conversation.row_version, clear_derived_memory: false })}>永久删除</Button></footer>
  </Dialog>;
}
