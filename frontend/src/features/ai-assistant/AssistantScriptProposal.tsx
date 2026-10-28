import { useEffect, useRef, useState } from 'react';
import { Button, Skeleton } from 'antd';
import { ApiError, errorMessage } from '../../api/http';
import { assistantScriptApi } from '../../api/modules/assistant-script';
import type { AssistantScriptProposal as Proposal } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';
import { flushStandardProjectSessions } from '../projects/standard-session-registry';
import { notifyAssistantBusinessChange } from './assistant-business-events';
import '../projects/script-desk/script-review.css';

export function AssistantScriptProposal({ operationId, disabled }: { operationId: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false), [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0);
  const [stale, setStale] = useState(false);
  const lock = useRef(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); setLoading(true); setError('');
    assistantScriptApi.preview(operationId, controller.signal)
      .then(value => { if (!controller.signal.aborted) setProposal(value); })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [open, operationId, revision]);
  async function apply() {
    if (!proposal || disabled || lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      if (!await flushStandardProjectSessions(proposal.project_id)) { setError('当前编辑尚未保存成功，请处理保存提示后再采用。'); return; }
      await assistantScriptApi.apply(operationId, proposal.content_version);
      notifyAssistantBusinessChange(proposal.project_id, ['script']);
      setRevision(value => value + 1);
    } catch (cause) {
      const expired = cause instanceof ApiError && cause.code === 'assistant_script_changed';
      setStale(expired);
      setError(expired ? '此建议基于旧版本，不能继续采用。请返回助手，引用当前剧本并重新提出修改要求。当前输入已保留。' : errorMessage(cause));
    }
    finally { lock.current = false; setBusy(false); }
  }
  return <><Button size="small" disabled={disabled} onClick={() => setOpen(true)}>审阅剧本修改</Button>
    {open && <Dialog title="审阅剧本修改" className="standard-creation-dialog assistant-script-review" canClose={!busy} onClose={() => setOpen(false)}>
      <div className="assistant-script-review-body">
        {loading ? <Skeleton active/> : proposal && <><p>基于正文版本 {proposal.content_version} · {proposal.start === proposal.end ? '插入新内容' : `替换第 ${proposal.start + 1}–${proposal.end} 字`}。</p>
          <div className="assistant-script-diff"><section><h3>修改前</h3><pre>{proposal.before || '空正文'}</pre></section><section><h3>修改后</h3><pre>{proposal.after || '移除此段文字'}</pre></section></div>
          {proposal.applied_version && <p role="status">已写入正文，保存版本 {proposal.applied_version}。</p>}</>}
        {(error || stale) && <div className="script-review-error" role="alert"><p>{stale ? '此建议基于旧版本，不能继续采用。请返回助手，引用当前剧本并重新提出修改要求。当前输入已保留。' : error}</p>
          {stale ? <Button onClick={() => setOpen(false)}>返回助手重新提出要求</Button> : <Button disabled={loading || busy} onClick={() => setRevision(value => value + 1)}>重新读取建议</Button>}
        </div>}
      </div><footer className="script-review-footer"><Button disabled={busy} onClick={() => setOpen(false)}>关闭</Button><Button type="primary" loading={busy} disabled={disabled || loading || !proposal || !!proposal.applied_version || stale} onClick={() => void apply()}>采用并写入正文</Button></footer>
    </Dialog>}
  </>;
}
