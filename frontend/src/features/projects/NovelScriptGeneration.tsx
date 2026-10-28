import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import type { WritingSession, WritingSnapshot } from './writing-session';
import type { NavigationBarrier } from './writing-navigation';
import { ScriptDesk } from './script-desk/ScriptDesk';
import { ScriptRewriteDialog } from './script-desk/ScriptRewriteDialog';
import { useWritingGeneration } from './script-desk/useWritingGeneration';
import { writingRewriteRequest, type ScriptSelection } from './script-desk/writing-requests';
import './script-desk/script-desk.css';

export function NovelScriptGeneration({ projectId, accountId, writing, session, readOnly, registerBarrier, model, onModel }: {
  projectId: string; accountId?: string; writing: WritingSnapshot; session: WritingSession; readOnly: boolean;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
  model: string; onModel: (model: string) => void;
}) {
  const barriers = useRef<{ generation: NavigationBarrier | null; settings: NavigationBarrier | null }>({ generation: null, settings: null });
  const registerGeneration = useCallback((barrier: NavigationBarrier | null) => { barriers.current.generation = barrier; }, []);
  const registerSettings = useCallback((barrier: NavigationBarrier | null) => { barriers.current.settings = barrier; }, []);
  useEffect(() => {
    registerBarrier?.({ hasUnsettled: () => Object.values(barriers.current).some(barrier => barrier?.hasUnsettled()), flush: async () => {
      for (const barrier of Object.values(barriers.current)) if (barrier?.hasUnsettled() && !await barrier.flush()) return false;
      return true;
    } });
    return () => registerBarrier?.(null);
  }, [registerBarrier]);
  const generation = useWritingGeneration(projectId, session, accountId, registerGeneration);
  const [selection, setSelection] = useState<ScriptSelection | null>(null);
  const [open, setOpen] = useState(false);
  const [instructions, setInstructions] = useState('');
  const [resolvedModel, setResolvedModel] = useState<string | undefined | null>(null);
  const close = useCallback(() => setOpen(false), []);
  const busy = writing.activeWritingTasks.some(task => task.target === 'script');
  function rewrite(next: ScriptSelection) {
    if (!selection || selection.source !== next.source || selection.start !== next.start || selection.end !== next.end) setInstructions('');
    setSelection(next); setOpen(true);
    if (!generation.uncertain && !generation.blocked) generation.setError('');
  }
  const feedback = generation.error && <StudioFeedback type="error" title="剧本创作" message={generation.error} noticeKey={`writing-operation:${projectId}:${generation.error}`}
    actions={<>
      {generation.uncertain && <><Button disabled={generation.submitting} onClick={() => void generation.resolve()}>核对原任务</Button><Button disabled={generation.submitting} onClick={() => void generation.resolve(true)}>按原请求重试</Button></>}
      {generation.blocked && <><Button onClick={generation.downloadRecovery}>下载原请求记录</Button><Button loading={generation.submitting} onClick={() => void generation.clearBlocked()}>已核对，清理记录</Button></>}
      {generation.lastTask?.can_resume && <Button disabled={generation.submitting} onClick={() => void generation.resume()}>恢复结果保存</Button>}
    </>}/>;
  return <>
    <ScriptDesk projectId={projectId} writing={writing} session={session} readOnly={readOnly}
      onRewrite={rewrite} selection={selection && (open || busy || generation.pendingTarget === 'script') ? selection : null}
      pendingTarget={generation.pendingTarget} submitting={generation.submitting} rewriteDisabled={generation.submitting || generation.uncertain || generation.blocked}
      onStop={task => void generation.stop(task)} stopping={generation.stopping} feedback={feedback} registerSettings={registerSettings}/>
    {selection && open && <ScriptRewriteDialog selection={selection} instructions={instructions} onInstructions={setInstructions}
      model={model} onModel={onModel} resolvedModel={resolvedModel} onResolvedModel={setResolvedModel}
      submitting={generation.submitting} disabled={readOnly || generation.uncertain || generation.blocked || busy || !resolvedModel}
      feedback={feedback} onClose={close} onSubmit={() => {
        const captured = selection;
        void generation.submit('script', () => writingRewriteRequest(projectId, session.getSnapshot(), captured, instructions, resolvedModel ?? '')).then(ok => { if (ok) close(); });
      }}/>}
  </>;
}
