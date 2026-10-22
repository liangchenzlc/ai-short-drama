import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Button, Input } from 'antd';
import { projectsApi, type RemoteProject } from '../../api/modules/projects';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { useAuth } from '../auth/AuthSession';
import { ResourceEditingSession, type ResourceRecovery } from './resource-editing-session';
import { creationScope, clearRecovery, readRecovery, readRecoveryText, saveRecovery } from './creation-recovery';
import { releaseStandardSession, retainStandardSession } from './standard-session-registry';
import type { NavigationBarrier } from './writing-navigation';

type NameDocument = { name: string };
function isNameRecovery(value: unknown): value is ResourceRecovery<NameDocument> {
  if (!value || typeof value !== 'object') return false;
  const item = value as ResourceRecovery<NameDocument>;
  return item.format === 1 && typeof item.base_version === 'string' && /^\d+$/.test(item.base_version)
    && typeof item.document?.name === 'string'
    && (!item.pending || typeof item.pending.base_version === 'string' && typeof item.pending.document?.name === 'string');
}

export function ProjectNameEditor({ project, readOnly, onUpdated, registerBarrier }: {
  project: RemoteProject; readOnly: boolean; onUpdated: (project: RemoteProject) => void;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
}) {
  const { user, enabled } = useAuth();
  const actor = user?.id ?? (enabled ? 'signed-out' : 'anonymous');
  const resourceKey = `project-name:${project.projectId}`;
  const session = useMemo(() => {
    const scope = creationScope(resourceKey, actor);
    return retainStandardSession(actor, resourceKey, () => new ResourceEditingSession<RemoteProject, NameDocument>({
      read: () => projectsApi.get(project.projectId),
      save: async (resource, document, assertActive) => {
        const name = document.name.trim();
        if (!name || Array.from(name).length > 120) throw new Error('名称不能为空，且最多 120 个字符。');
        assertActive();
        return projectsApi.update(project.projectId, { name, row_version: resource.rowVersion });
      },
      version: resource => resource.rowVersion,
      document: resource => ({ name: resource.name }),
      apply: (resource, document) => ({ ...resource, name: document.name }),
      recovery: {
        read: () => readRecovery(scope, isNameRecovery),
        save: value => saveRecovery(scope, value), clear: () => clearRecovery(scope),
        export: () => readRecoveryText(scope), previous: () => readRecoveryText(`${scope}:previous`),
        backup: raw => saveRecovery(`${scope}:previous`, { raw }),
      },
    }), { projectId: project.projectId, label: '作品名称' });
  }, [actor, resourceKey, project.projectId]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const editingRef = useRef(false);
  const [error, setError] = useState('');
  useEffect(() => { session.initialize(project); }, [session, project]);
  useEffect(() => {
    registerBarrier(session);
    return () => { registerBarrier(null); releaseStandardSession(actor, resourceKey); };
  }, [session, actor, resourceKey, registerBarrier]);
  useEffect(() => {
    if (state.value && state.status === 'saved' && (state.value.rowVersion !== project.rowVersion || state.value.name !== project.name)) onUpdated(state.value);
  }, [state.value, state.status, project.rowVersion, project.name, onUpdated]);
  async function reload(keepLocal: boolean) {
    try { setError(''); await session.reload(keepLocal); } catch (cause) { setError(cause instanceof Error ? cause.message : '名称读取失败，请重试。'); }
  }
  function beginEditing() {
    setDraft(state.value?.name ?? project.name); editingRef.current = true; setEditing(true);
  }
  function finishEditing(commit: boolean) {
    if (!editingRef.current) return;
    editingRef.current = false; setEditing(false);
    if (commit) { session.edit({ name: draft }); void session.flush(); }
  }
  function download() {
    try {
      const raw = session.exportRecovered() ?? JSON.stringify({ name: state.value?.name });
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `project-${project.projectId}-name.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '名称备份下载失败。'); }
  }
  return <>
    {editing && !readOnly ? <Input autoFocus className="episode-project-name-input" aria-label="作品名称" maxLength={120} value={draft}
      disabled={state.recoveryBlocked} onChange={event => setDraft(event.target.value)}
      onBlur={() => finishEditing(true)}
      onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') { event.preventDefault(); finishEditing(false); } }}/>
      : <button type="button" className="episode-project-name" title={readOnly ? project.name : '点击编辑作品名称'} disabled={readOnly} onClick={beginEditing}>{state.value?.name ?? project.name}</button>}
    {(error || state.message) && <StudioFeedback type={state.status === 'conflict' || state.recoveryBlocked ? 'warning' : 'error'} title="作品名称保存" message={error || state.message} noticeKey={`project-name:${project.projectId}`}
      actions={<><Button size="small" onClick={download}>下载本机名称</Button>{!state.recoveryBlocked && <Button size="small" disabled={state.busy} onClick={() => void reload(true)}>核对后保留本机名称</Button>}<Button size="small" disabled={state.busy} onClick={() => void reload(false)}>使用服务端名称</Button></>}/>} 
  </>;
}
