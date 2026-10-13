import { Alert, Button } from 'antd';
import { confirmAction } from '../../components/ui/confirm';
import type { useCreationDraft } from './useCreationDraft';

export function CreationDraftFeedback({ draft }: { draft: ReturnType<typeof useCreationDraft> }) {
  if (!draft.message && draft.status !== 'local') return null;
  if (draft.status === 'local') return <p role="status" className="creation-draft-feedback">本机生成草稿已保留，尚未保存到服务端。</p>;
  return <Alert type={draft.status === 'conflict' ? 'warning' : 'error'} showIcon message={draft.message} action={<>
    {draft.status !== 'conflict' && <Button size="small" onClick={() => void draft.retry()}>重试保存设置</Button>}
    <Button size="small" onClick={async () => { if (await confirmAction('载入服务端生成设置会替换本机输入，确定继续？')) await draft.reload(); }}>载入服务端设置</Button>
  </>}/>;
}
