import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Button, Input, Select, Spin } from 'antd';
import { BookOpen, RefreshCw, X } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { projectsApi, type RemoteProject } from '../../api/modules/projects';
import { ApiError, errorMessage } from '../../api/http';
import type { AssistantMentionReference, AssistantMessageContext, AssistantReference, AssistantSelectedSubject, AssistantSubjectDraft, AssistantSubjectKind, AssistantSubjectMetadata, AssistantSubjectOption, AssistantSubjectReference, AssistantSubjectSelection, AssistantSubjectStatus, AssistantTask } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';
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
const labels: Record<AssistantSubjectKind, string> = { asset: '素材', shot: '分镜', node: '画布节点', image_group: '图片组', image_slot: '图位', sound_line: '声音台词' };

export const AssistantSubjects = forwardRef<AssistantSubjectsController, {
  task: AssistantTask | null; projectId: string; canvasId?: string | null; contextKey?: string;
  value: AssistantSubjectDraft | null; onChange: (value: AssistantSubjectDraft | null) => void;
  disabled: boolean; layout?: 'standard';
  prepareContext: (include: boolean, references?: readonly AssistantReference[]) => Promise<AssistantMessageContext | null>;
}>(function AssistantSubjects({ task, projectId, canvasId, contextKey, value, onChange, disabled, layout, prepareContext }, ref) {
  const [open, setOpen] = useState(false);
  const [metadata, setMetadata] = useState<AssistantSubjectMetadata | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [projects, setProjects] = useState<RemoteProject[]>([]);
  const [projectQuery, setProjectQuery] = useState('');
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [projectsError, setProjectsError] = useState('');
  const [selectedProject, setSelectedProject] = useState(projectId);
  const [kind, setKind] = useState<AssistantSubjectKind>(canvasId ? 'node' : 'asset');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [options, setOptions] = useState<AssistantSubjectOption[]>([]);
  const [total, setTotal] = useState(0);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [directoryError, setDirectoryError] = useState('');
  const [adding, setAdding] = useState('');
  const location = useRef(contextKey); location.current = contextKey;
  const currentMetadata = metadata?.task_id === task?.id ? metadata : null;
  const selected = currentMetadata?.items ?? task?.selected_subjects ?? [];
  const draft = value?.taskId === (task?.id ?? null) ? value : null;
  const staleDraft = !!value && (!draft || !!currentMetadata && draft.expectedInputRevision !== currentMetadata.input_revision);
  const retained = draft ? selected.filter(item => draft.retainKeys.includes(item.subject_key)) : selected;
  const added = draft?.added ?? [];
  const locked = disabled || loading || !!adding;
  const selectedMode = projects.find(item => item.projectId === selectedProject)?.workspaceMode ?? (selectedProject === projectId && canvasId ? 'infinite_canvas' : 'standard');
  useEffect(() => {
    const update = () => setReload(old => old + 1);
    window.addEventListener('focus', update);
    return () => window.removeEventListener('focus', update);
  }, []);
  useEffect(() => {
    if (!task || !open && !value && !task.selected_subjects.length) { setMetadata(null); return; }
    const controller = new AbortController(); setLoading(true); setError('');
    assistantApi.subjects(task.id, controller.signal).then(next => { if (!controller.signal.aborted) setMetadata(next); })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [task?.id, task?.input_revision, task?.selected_subjects.length, contextKey, open, !!value, reload]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); setProjectsLoading(true); setProjectsError('');
    const timer = setTimeout(() => projectsApi.list(0, projectQuery, controller.signal)
      .then(page => { if (!controller.signal.aborted) setProjects(page.items); })
      .catch(cause => { if (!controller.signal.aborted) setProjectsError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setProjectsLoading(false); }), 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, projectQuery, reload]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); setDirectoryLoading(true); setDirectoryError(''); setOptions([]);
    const timer = setTimeout(() => assistantApi.subjectDirectory(selectedProject, kind, query, offset, controller.signal)
      .then(page => { if (!controller.signal.aborted) { setOptions(page.items); setTotal(page.total); } })
      .catch(cause => { if (!controller.signal.aborted) setDirectoryError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setDirectoryLoading(false); }), 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, selectedProject, kind, query, offset, reload]);
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
        project_name: projects.find(project => project.projectId === projectId)?.name ?? '当前作品', source_name: context.id }));
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
      if (staleDraft) throw new ApiError('任务引用已变化，请打开“管理任务引用”核对草稿。', 'assistant_selection_changed');
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
  async function add(option: AssistantSubjectOption) {
    if (locked || staleDraft) return;
    setAdding(identity(option)); setError('');
    try {
      let chosen = option;
      const isLocal = option.project_id === projectId && (option.kind === 'node' ? option.source_id === canvasId : ['asset', 'shot'].includes(option.kind));
      if (isLocal && (option.kind === 'asset' || option.kind === 'shot' || option.kind === 'node')) {
        await prepareContext(false, [{ kind: option.kind, id: option.id, revision: option.kind === 'node' ? undefined : option.revision }]);
        const [updated] = await assistantApi.inspectSubjects([reference(option)]);
        chosen = { ...option, ...updated };
      }
      const next = base();
      const existing = selected.find(item => identity(item) === identity(chosen));
      const retainKeys = next.retainKeys.filter(key => key !== existing?.subject_key);
      const others = next.added.filter(item => identity(item) !== identity(chosen));
      if (retainKeys.length + others.length >= 16) throw new ApiError('一个任务最多选入 16 个主体。', 'assistant_context_limit');
      onChange({ ...next, retainKeys, added: [...others, chosen] });
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setAdding(''); }
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
    <button type="button" className="ai-assistant-chip" disabled={disabled} onClick={() => setOpen(true)}><BookOpen size={13}/>管理任务引用</button>
    {retained.map(item => <span className="ai-assistant-chip" key={item.subject_key} title={`${item.project_name} · ${item.name} · v${item.revision}`}>
      <span>{item.name} · v{item.revision}{'availability' in item && item.availability === 'changed' ? ' · 有新版本' : 'availability' in item && item.availability === 'unavailable' ? ' · 不可用' : ''}</span>
      <button type="button" aria-label={`移除任务引用${item.name}`} disabled={locked || staleDraft} onClick={() => remove(item)}><X size={12}/></button>
    </span>)}
    {added.map(item => <span className="ai-assistant-chip" key={identity(item)} title={`${item.project_name} · ${item.name}`}><span>{item.name} · 待发送</span>
      <button type="button" aria-label={`移除待发送引用${item.name}`} disabled={locked} onClick={() => onChange({ ...base(), added: added.filter(old => identity(old) !== identity(item)) })}><X size={12}/></button></span>)}
    {loading && <Spin size="small"/>}
    {currentMetadata?.pending && <span className="ai-assistant-meta">{task?.status === 'stopped' ? '引用变更在继续后生效' : '引用变更等待工具边界生效'}</span>}
  </div>;
  return <>{chips}
    {error && !open && <StudioFeedback type="error" title="任务引用" message={error} noticeKey={`assistant-subjects:${task?.id ?? 'new'}`} actions={<Button size="small" onClick={() => setReload(old => old + 1)}>重新读取</Button>}/>}
    {open && <Dialog title="管理任务引用" className={`ai-assistant-context-dialog ai-assistant-subjects-modal${layout === 'standard' ? ' standard-creation-dialog standard-assistant-dialog' : ''}`} canClose={!adding} onClose={() => setOpen(false)}>
      <div className="agent-context-dialog-body ai-assistant-subjects-dialog">
        <p className="ai-assistant-meta">选择已保存的素材、分镜、节点、图片组、图位或声音台词。引用在本次任务有效；发送后在工具边界应用，新任务需重新选入。</p>
        {error && <StudioFeedback type="error" title="任务引用" message={error} noticeKey="assistant-subjects-dialog" actions={<Button size="small" onClick={() => setReload(old => old + 1)}>重新读取</Button>}/>}
        {staleDraft && <StudioFeedback type="warning" persistent title="引用草稿需要核对" message="任务已收到其他输入。保留当前草稿，或明确采用当前任务引用后重新编辑。" noticeKey="assistant-subjects-stale" actions={<Button size="small" onClick={() => { onChange(null); setReload(old => old + 1); }}>采用当前任务引用</Button>}/>}
        <div className="ai-assistant-subjects-current" aria-label="引用版本详情">
          {loading ? <Spin/> : !retained.length && !added.length ? <p className="ai-assistant-meta">尚未选入主体。</p> : null}
          {retained.map(item => <div key={item.subject_key} className="ai-assistant-subject-row"><div><strong>{item.name}</strong><span>{item.project_name} · {labels[item.kind]} · 已读取 v{item.revision}</span>
            {'availability' in item && item.availability === 'changed' && <span>有新版本；当前仍使用原快照</span>}
            {'availability' in item && item.availability === 'unavailable' && <span>来源已删除或无权访问，不能继续读取</span>}</div>
            <div>{(!('availability' in item) || item.availability !== 'unavailable') && assistantSourceLink(item) && <Button size="small" href={assistantSourceLink(item)!} target="_blank" rel="noreferrer">查看来源</Button>}
            {'availability' in item && item.availability === 'changed' && <Button size="small" icon={<RefreshCw size={12}/>} disabled={locked || staleDraft} onClick={() => refresh(item as AssistantSubjectStatus)}>刷新引用</Button>}
            <Button size="small" disabled={locked || staleDraft} onClick={() => remove(item)}>移除</Button></div></div>)}
          {added.map(item => <div key={identity(item)} className="ai-assistant-subject-row"><div><strong>{item.name}</strong><span>{item.project_name} · v{item.revision} · 待发送</span></div><Button size="small" disabled={locked} onClick={() => onChange({ ...base(), added: added.filter(old => identity(old) !== identity(item)) })}>移除</Button></div>)}
        </div>
        <label>来源作品<Select aria-label="引用来源作品" showSearch filterOption={false} value={selectedProject} loading={projectsLoading} disabled={locked || staleDraft} onSearch={setProjectQuery}
          options={[{ value: projectId, label: '当前作品' }, ...projects.filter(item => item.projectId !== projectId).map(item => ({ value: item.projectId, label: item.name }))]}
          onChange={id => { setSelectedProject(id); setKind(projects.find(item => item.projectId === id)?.workspaceMode === 'infinite_canvas' || id === projectId && canvasId ? 'node' : 'asset'); setOffset(0); setQuery(''); }}/></label>
        {projectsError && <StudioFeedback type="error" title="读取来源作品" message={projectsError} noticeKey="assistant-subject-projects" actions={<Button size="small" onClick={() => setReload(old => old + 1)}>重新读取</Button>}/>}
        <label>主体类型<Select aria-label="引用主体类型" value={kind} disabled={locked || staleDraft} options={(selectedMode === 'infinite_canvas' ? ['node'] as const : ['asset', 'shot', 'image_group', 'image_slot', 'sound_line'] as const).map(value => ({ value, label: labels[value] }))} onChange={value => { setKind(value); setOffset(0); }}/></label>
        <Input aria-label="搜索引用主体" placeholder="搜索名称或图片组、图位 ID" value={query} maxLength={120} disabled={locked || staleDraft} onChange={event => { setQuery(event.target.value); setOffset(0); }}/>
        <div className="ai-assistant-subjects-options" aria-label="可选主体" aria-busy={directoryLoading}>
          {directoryLoading ? <Spin/> : directoryError ? <StudioFeedback type="error" title="读取主体目录" message={directoryError} noticeKey="assistant-subject-directory" actions={<Button size="small" onClick={() => setReload(old => old + 1)}>重新读取</Button>}/>
            : !options.length ? <p className="ai-assistant-meta">没有符合条件的已保存主体。</p> : options.map(item => <div key={identity(item)} className="ai-assistant-subject-row"><div><strong>{item.name}</strong><span>{item.source_name} · v{item.revision}</span></div><Button size="small" loading={adding === identity(item)} disabled={locked || staleDraft || added.some(old => identity(old) === identity(item)) || retained.some(old => identity(old) === identity(item))} onClick={() => void add(item)}>选入</Button></div>)}
        </div>
        {total > 20 && <div className="ai-assistant-history-footer"><Button size="small" disabled={!offset || directoryLoading} onClick={() => setOffset(old => Math.max(0, old - 20))}>上一页</Button><span>{offset + 1}–{Math.min(offset + 20, total)} / {total}</span><Button size="small" disabled={offset + 20 >= total || directoryLoading} onClick={() => setOffset(old => old + 20)}>下一页</Button></div>}
      </div>
      <footer className="ai-assistant-history-footer"><span className="ai-assistant-meta">变更随下一条消息发送</span><Button disabled={!!adding} onClick={() => setOpen(false)}>完成</Button></footer>
    </Dialog>}
  </>;
});
