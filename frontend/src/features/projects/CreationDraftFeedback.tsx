import { useRef, useState } from 'react';
import { Alert, Button } from 'antd';
import { errorMessage } from '../../api/http';
import type { useCreationDraft } from './useCreationDraft';

export function CreationDraftFeedback({ draft }: { draft: ReturnType<typeof useCreationDraft> }) {
  const [busy, setBusy] = useState<'retry' | 'reload' | ''>('');
  const locked = useRef(false);
  const [error, setError] = useState('');
  async function perform(action: 'retry' | 'reload', operation: () => Promise<unknown>) {
    if (locked.current) return;
    locked.current = true; setBusy(action); setError('');
    try { await operation(); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { locked.current = false; setBusy(''); }
  }
  if (draft.status !== 'error' && draft.status !== 'conflict' && !busy) return null;
  return <Alert type={busy ? 'info' : draft.status === 'conflict' ? 'warning' : 'error'} showIcon message={error || (busy === 'reload' ? '正在读取服务端设置…' : busy === 'retry' ? '正在核对保存结果…' : draft.message)} action={<>
    {draft.status !== 'conflict' && <Button size="small" aria-label="重试保存设置" loading={busy === 'retry'} disabled={!!busy} onClick={() => void perform('retry', draft.retry)}>重试保存设置</Button>}
    <Button size="small" aria-label="载入服务端设置" loading={busy === 'reload'} disabled={!!busy} onClick={() => void perform('reload', draft.reload)}>载入服务端设置</Button>
  </>}/>;
}
