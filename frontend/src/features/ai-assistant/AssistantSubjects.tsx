import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Button, Spin } from 'antd';
import { RefreshCw, X } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { ApiError, errorMessage } from '../../api/http';
import type { AssistantMentionReference, AssistantMessageContext, AssistantReference, AssistantSelectedSubject, AssistantSubjectDraft, AssistantSubjectMetadata, AssistantSubjectOption, AssistantSubjectReference, AssistantSubjectSelection, AssistantSubjectStatus, AssistantTask } from '../../api/types/assistant';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { assistantSourceLink } from './assistant-source-link';

export interface AssistantSubjectsController {
  prepare: (localContext: AssistantMessageContext | null) => Promise<AssistantSubjectSelection | null>;
  addLocal: (references: readonly AssistantMentionReference[]) => Promise<void>;
}
const identity = (item: AssistantSubjectReference) => JSON.stringify([item.project_id, item.source_kind, item.source_id, item.kind, item.id]);
const reference = (item: AssistantSubjectReference): AssistantSubjectReference => ({
  project_id: item.project_id, source_kind: item.source_kind, source_id: item.source_id, kind: item.kind,
  id: item.id, revision: item.revision, ...(item.content_revision ? { content_revision: item.content_revision } : {}),
  ...(item.checksum ? { checksum: item.checksum } : {}),
});


export const AssistantSubjects = forwardRef<AssistantSubjectsController, {
  task: AssistantTask | null; projectId: string; canvasId?: string | null; contextKey?: string;
  value: AssistantSubjectDraft | null; onChange: (value: AssistantSubjectDraft | null) => void;
  disabled: boolean;
  prepareContext: (include: boolean, references?: readonly AssistantReference[]) => Promise<AssistantMessageContext | null>;
}>(function AssistantSubjects({ task, projectId, canvasId, contextKey, value, onChange, disabled, prepareContext }, ref) {
  const [metadata, setMetadata] = useState<AssistantSubjectMetadata | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const location = useRef(contextKey); location.current = contextKey;
  const currentMetadata = metadata?.task_id === task?.id ? metadata : null;
  const selected = currentMetadata?.items ?? task?.selected_subjects ?? [];
  const draft = value?.taskId === (task?.id ?? null) ? value : null;
  const staleDraft = !!value && (!draft || !!currentMetadata && draft.expectedInputRevision !== currentMetadata.input_revision);
  const retained = draft ? selected.filter(item => draft.retainKeys.includes(item.subject_key)) : selected;
  const added = draft?.added ?? [];
  const locked = disabled || loading;
  useEffect(() => {
    const update = () => setReload(old => old + 1);
    window.addEventListener('focus', update);
    return () => window.removeEventListener('focus', update);
  }, []);
  useEffect(() => {
    if (!task || !value && !task.selected_subjects.length) { setMetadata(null); return; }
    const controller = new AbortController(); setLoading(true); setError('');
    assistantApi.subjects(task.id, controller.signal).then(next => { if (!controller.signal.aborted) setMetadata(next); })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [task?.id, task?.input_revision, task?.selected_subjects.length, contextKey, !!value, reload]);
  function base(meta = currentMetadata): AssistantSubjectDraft {
    return draft ?? { taskId: task?.id ?? null, expectedInputRevision: meta?.input_revision ?? task?.input_revision ?? '0',
      retainKeys: (meta?.items ?? selected).map(item => item.subject_key), added: [] };
  }
  useImperativeHandle(ref, () => ({
    async addLocal(references) {
      if (staleDraft) throw new ApiError('任务引用已变化，请先核对引用草稿。', 'assistant_selection_changed');
      const captured = contextKey;
      const context = await prepareContext(false, references.map(({ kind, id, revision }) => ({ kind, id, revision })));
      if (!context?.selected?.length) throw new ApiError('当前主体尚未保存或已不可用。', 'assistant_context_blocked');
      const inspected = await assistantApi.inspectSubjects(context.selected.map(item => ({
        project_id: projectId, source_kind: context.kind, source_id: context.id,
        kind: item.kind, id: item.id, revision: item.revision ?? context.revision,
      })));
      if (location.current !== captured) throw new ApiError('页面在读取期间变化，请重新选择引用。', 'assistant_context_changed');
      const meta = task ? currentMetadata ?? await assistantApi.subjects(task.id) : null;
      if (location.current !== captured) throw new ApiError('页面在读取期间变化，请重新选择引用。', 'assistant_context_changed');
      const next = base(meta);
      const incoming = inspected.map(item => ({ ...item, name: references.find(r => r.kind === item.kind && r.id === item.id)?.name ?? item.id,
        project_name: '当前作品', source_name: context.id }));
      const retained = next.retainKeys.filter(key => !incoming.some(item => {
        const old = meta?.items.find(candidate => candidate.subject_key === key) ?? selected.find(candidate => candidate.subject_key === key);
        return old && identity(old) === identity(item);
      }));
      const added = [...next.added.filter(item => !incoming.some(next => identity(item) === identity(next))), ...incoming];
      if (retained.length + added.length > 16) throw new ApiError('一个任务最多选入 16 个主体。', 'assistant_context_limit');
      onChange({ ...next, retainKeys: retained, added });
    },
    async prepare(localContext) {
      if (!value && !localContext?.selected?.length) return null;
      if (staleDraft) throw new ApiError('任务引用已变化，请核对引用草稿后再发送。', 'assistant_selection_changed');
      const meta = task ? currentMetadata ?? await assistantApi.subjects(task.id) : null;
      const selection = base(meta);
      let incoming = [...selection.added];
      const localAdded = incoming.filter(item => item.project_id === projectId && (item.kind === 'node' ? item.source_id === canvasId : ['asset', 'shot'].includes(item.kind)));
      if (localAdded.length) await prepareContext(false, localAdded.map(item => ({
        kind: item.kind as AssistantReference['kind'], id: item.id, revision: item.kind === 'node' ? undefined : item.revision,
      })));
      if (localContext?.selected?.length) {
        const local = localContext.selected.map(item => ({ project_id: projectId, source_kind: localContext.kind,
          source_id: localContext.id, kind: item.kind, id: item.id, revision: item.revision ?? localContext.revision }));
        const inspected = await assistantApi.inspectSubjects(local);
        if (local.some((item, index) => localContext.selected![index].revision && item.revision !== inspected[index].revision)) {
          throw new ApiError('引用对象在保存后再次变化，请重新选择。', 'assistant_context_changed');
        }
        incoming = [...incoming.filter(item => !inspected.some(next => identity(item) === identity(next))),
          ...inspected.map(item => ({ ...item, name: item.id, project_name: '', source_name: '' }))];
      }
      const keys = selection.retainKeys.filter(key => !incoming.some(item => {
        const old = meta?.items.find(candidate => candidate.subject_key === key) ?? selected.find(candidate => candidate.subject_key === key);
        return old && identity(old) === identity(item);
      }));
      if (keys.length + incoming.length > 16) throw new ApiError('一个任务最多选入 16 个主体，请减少引用。', 'assistant_context_limit');
      return { expected_input_revision: selection.expectedInputRevision, retain_keys: keys, subjects: incoming.map(reference) };
    },
  }));
  function remove(item: AssistantSelectedSubject) {
    const next = base(); onChange({ ...next, retainKeys: next.retainKeys.filter(key => key !== item.subject_key) });
  }
  function refresh(item: AssistantSubjectStatus) {
    if (!item.current_revision) return;
    const next = base();
    const chosen: AssistantSubjectOption = { ...reference(item), revision: item.current_revision,
      content_revision: item.current_content_revision, checksum: item.current_checksum,
      name: item.name, project_name: item.project_name, source_name: item.source_id };
    onChange({ ...next, retainKeys: next.retainKeys.filter(key => key !== item.subject_key),
      added: [...next.added.filter(old => identity(old) !== identity(chosen)), chosen] });
  }
  const chips = <div className="ai-assistant-chips" aria-label="当前任务引用">
    {retained.map(item => <span className="ai-assistant-chip" key={item.subject_key} title={`${item.project_name} · ${item.name} · v${item.revision}`}>
      {assistantSourceLink(item) ? <a href={assistantSourceLink(item)!} target="_blank" rel="noreferrer" aria-label={`查看引用来源${item.name}`}>{item.name} · v{item.revision}</a> : <span>{item.name} · v{item.revision}</span>}
      {'availability' in item && item.availability === 'changed' ? <span>有新版本</span> : 'availability' in item && item.availability === 'unavailable' ? <span>不可用</span> : null}
      {'availability' in item && item.availability === 'changed' && <button type="button" aria-label={`刷新引用${item.name}`} disabled={locked || staleDraft} onClick={() => refresh(item as AssistantSubjectStatus)}><RefreshCw size={12}/></button>}
      <button type="button" aria-label={`移除任务引用${item.name}`} disabled={locked || staleDraft} onClick={() => remove(item)}><X size={12}/></button>
    </span>)}
    {added.map(item => <span className="ai-assistant-chip" key={identity(item)} title={`${item.project_name} · ${item.name}`}><span>{item.name} · 待发送</span>
      <button type="button" aria-label={`移除待发送引用${item.name}`} disabled={locked} onClick={() => onChange({ ...base(), added: added.filter(old => identity(old) !== identity(item)) })}><X size={12}/></button></span>)}
    {loading && <Spin size="small"/>}
    {currentMetadata?.pending && <span className="ai-assistant-meta">{task?.status === 'stopped' ? '引用变更在继续后生效' : '引用变更等待工具边界生效'}</span>}
  </div>;
  return <>{chips}
    {error && <StudioFeedback type="error" title="任务引用" message={error} noticeKey={`assistant-subjects:${task?.id ?? 'new'}`} actions={<Button size="small" onClick={() => setReload(old => old + 1)}>重新读取</Button>}/>}
    {staleDraft && <StudioFeedback type="warning" persistent title="引用草稿需要核对" message="任务已收到其他输入。采用当前任务引用后，可重新添加引用。" noticeKey="assistant-subjects-stale" actions={<Button size="small" onClick={() => { onChange(null); setReload(old => old + 1); }}>采用当前任务引用</Button>}/>}
  </>;
});
