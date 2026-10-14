import { useRef, useState } from 'react';
import { Alert, Button } from 'antd';
import { confirmAction } from '../../components/ui/confirm';
import type { useCreationDraft } from './useCreationDraft';

export function CreationDraftFeedback({ draft }: { draft: ReturnType<typeof useCreationDraft> }) {
  const [busy, setBusy] = useState<'retry' | 'reload' | ''>('');
  const locked = useRef(false);
  async function perform(action: 'retry' | 'reload', operation: () => Promise<unknown>) {
    if (locked.current) return;
    locked.current = true; setBusy(action);
    try { await operation(); }
    finally { locked.current = false; setBusy(''); }
  }
  if (!draft.message && draft.status !== 'local') return null;
  if (draft.status === 'local') return <p role="status" className="creation-draft-feedback">本机生成草稿已保留，尚未保存到服务端。</p>;
  return <Alert type={draft.status === 'conflict' ? 'warning' : 'error'} showIcon message={draft.message} action={<>
    {draft.status !== 'conflict' && <Button size="small" aria-label="重试保存设置" loading={busy === 'retry'} disabled={!!busy} onClick={() => void perform('retry', draft.retry)}>重试保存设置</Button>}
    <Button size="small" aria-label="载入服务端设置" loading={busy === 'reload'} disabled={!!busy} onClick={() => void perform('reload', async () => {
      if (await confirmAction('载入服务端生成设置会替换本机输入，确定继续？')) await draft.reload();
    })}>载入服务端设置</Button>
  </>}/>;
}
