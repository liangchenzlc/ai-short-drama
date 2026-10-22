import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Dropdown, Segmented, Skeleton, Switch } from 'antd';
import { BookOpen, Ellipsis, Plus, Search, Upload } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { agentsApi } from '../../api/modules/agents';
import type { AgentSkill, AgentSkillDetail, AgentSkillFilters } from '../../api/types/agents';
import { ApiError, errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { ListToolbar } from '../../components/ui/Workspace';
import { StudioFeedback, StudioFeedbackIndicator, StudioMessageProvider, useStudioMessage } from '../../components/ui/StudioFeedback';
import { confirmAction } from '../../components/ui/confirm';
import { workflowStorage } from '../auth/account-storage';
import { SkillEditor } from './SkillEditor';
import { SkillImportDialog } from './SkillImportDialog';
import { clearSkillDraft, createSkillDraft, downloadSkillText, rawSkillAction, rawSkillDraft, readSkillAction, readSkillDraft,
  saveSkillAction, saveSkillDraft, type SkillDraft, type SkillPendingAction } from './skill-draft';
import './skill-manager.css';

export function SkillManager(props: { accountId: string; onGuardChange: (guard: (() => Promise<boolean>) | null) => void }) {
  const host = useRef<HTMLDivElement>(null);
  return <div className="skill-manager studio-feedback-host" ref={host}><StudioMessageProvider local getContainer={() => host.current ?? document.body}>
    <SkillManagerBody {...props}/>
  </StudioMessageProvider></div>;
}
function SkillManagerBody({ accountId, onGuardChange }: { accountId: string; onGuardChange: (guard: (() => Promise<boolean>) | null) => void }) {
  const [storage] = useState(workflowStorage);
  const [restored] = useState(() => ({ ...readSkillDraft(accountId, storage), ...readSkillAction(accountId, storage) }));
  const [draft, setDraft] = useState<SkillDraft | null>(restored.draft);
  const [draftError, setDraftError] = useState(() => readSkillDraft(accountId, storage).error);
  const [actionError, setActionError] = useState(restored.error);
  const [editorOpen, setEditorOpen] = useState(!!restored.draft);
  const [readOnly, setReadOnly] = useState(!!restored.draft?.base?.builtin);
  const [pendingAction, setPendingAction] = useState<SkillPendingAction | null>(restored.action);
  const [operationOpen, setOperationOpen] = useState(!!restored.action);
  const [operationIssue, setOperationIssue] = useState(restored.action ? '此操作的结果待核对，请继续处理原请求。' : '');
  const [actionLatest, setActionLatest] = useState<AgentSkillDetail | null>(null);
  const [source, setSource] = useState<'personal' | 'builtin'>('personal');
  const [query, setQuery] = useState(''); const [search, setSearch] = useState('');
  const [enabled, setEnabled] = useState('all'); const [invocation, setInvocation] = useState('all');
  const [mode, setMode] = useState('all'); const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AgentSkill[]>([]); const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true); const [listError, setListError] = useState('');
  const [revision, setRevision] = useState(0); const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState(''); const [importOpen, setImportOpen] = useState(false);
  const [deleting, setDeleting] = useState<AgentSkill | null>(null);
  const live = useRef(true); const lock = useRef(false); const leaveLock = useRef(false); const allowUnload = useRef(false);
  const snapshot = useRef({ draft, editorOpen, pendingAction, busy }); snapshot.current = { draft, editorOpen, pendingAction, busy };
  const navigate = useNavigate(); const message = useStudioMessage();
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setListError('');
    const filters: AgentSkillFilters = { source, ...(search ? { query: search } : {}),
      ...(enabled === 'all' ? {} : { enabled: enabled === 'enabled' }),
      ...(invocation === 'all' ? {} : { invocation_mode: invocation as 'auto' | 'manual' }),
      ...(mode === 'all' ? {} : { mode: mode as 'standard' | 'infinite_canvas' }) };
    agentsApi.skills(offset, controller.signal, filters).then(page => {
      if (controller.signal.aborted) return;
      if (!page.items.length && offset > 0) { setOffset(Math.max(0, Math.ceil(page.total / 50) - 1) * 50); return; }
      setItems(page.items); setTotal(page.total);
    }).catch(cause => { if (!controller.signal.aborted) setListError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [source, search, enabled, invocation, mode, offset, revision]);

  const guard = useCallback(async () => {
    const state = snapshot.current;
    if (state.busy || leaveLock.current) return false;
    if (!state.pendingAction && !(state.editorOpen && (state.draft?.changed || state.draft?.pending))) return true;
    leaveLock.current = true;
    try {
      const allowed = await confirmAction('Skill 有尚未保存或待核对的操作。保留草稿和原请求并离开当前页面？',
        { title: '保留 Skill 草稿', confirmText: '保留并离开', danger: false });
      if (allowed) { allowUnload.current = true; setTimeout(() => { allowUnload.current = false; }, 2000); }
      return allowed;
    }
    finally { leaveLock.current = false; }
  }, []);
  useEffect(() => { onGuardChange(guard); return () => onGuardChange(null); }, [guard, onGuardChange]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      const state = snapshot.current;
      if (!allowUnload.current && (state.busy || state.pendingAction || state.editorOpen && (state.draft?.changed || state.draft?.pending))) {
        event.preventDefault(); event.returnValue = '';
      }
    };
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const anchor = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href]') : null;
      if (!anchor || anchor.download || anchor.target && anchor.target !== '_self') return;
      const target = new URL(anchor.href, window.location.href);
      if (target.pathname === window.location.pathname && target.search === window.location.search || anchor.getAttribute('href')?.startsWith('#')) return;
      const state = snapshot.current;
      if (!state.busy && !state.pendingAction && !(state.editorOpen && (state.draft?.changed || state.draft?.pending))) return;
      event.preventDefault(); event.stopPropagation();
      void guard().then(allowed => {
        if (!allowed || !live.current) return;
        if (target.origin === window.location.origin) navigate(target.pathname + target.search + target.hash);
        else { allowUnload.current = true; window.location.assign(target.href); }
      });
    };
    window.addEventListener('beforeunload', unload); document.addEventListener('click', click, true);
    return () => { window.removeEventListener('beforeunload', unload); document.removeEventListener('click', click, true); };
  }, [guard, navigate]);

  function updateDraft(next: SkillDraft, settled = false) {
    const saved = saveSkillDraft(accountId, next, storage, settled);
    setDraft(next); setDraftError(saved ? '' : '浏览器未能保存 Skill 草稿。输入仍在，请下载草稿并恢复存储后重试。');
    return saved;
  }
  function canOpenDraft() {
    if (draftError || pendingAction || draft?.pending || draft?.changed) {
      setIssue('请先恢复并处理已有草稿或待核对操作，再开始另一份 Skill。'); return false;
    }
    return true;
  }
  function newDraft(next: SkillDraft) {
    if (!canOpenDraft()) return;
    updateDraft(next); setReadOnly(false); setEditorOpen(true); setImportOpen(false); setIssue('');
  }
  async function openItem(item: AgentSkill, edit = false) {
    if (lock.current || busy || pendingAction) return;
    if (draft?.base?.id === item.id && (draft.changed || draft.pending)) { setEditorOpen(true); setReadOnly(false); return; }
    if (!canOpenDraft()) return;
    lock.current = true; setBusy(true); setIssue('');
    try {
      const detail = await agentsApi.skill(item.id);
      if (!live.current) return;
      updateDraft(createSkillDraft(detail)); setReadOnly(detail.builtin || !edit); setEditorOpen(true);
    } catch (cause) { if (live.current) setIssue(`读取 Skill 失败：${errorMessage(cause)}`); }
    finally { lock.current = false; if (live.current) setBusy(false); }
  }
  function saved(detail: AgentSkillDetail) {
    if (!clearSkillDraft(accountId, storage)) { setDraftError('Skill 已收到成功回执，但本机记录尚未清除。请继续使用原请求核对，避免重复创建。'); return; }
    setDraft(null); setEditorOpen(false); setDraftError(''); setRevision(value => value + 1);
    message.success(detail.compatible ? 'Skill 已保存。' : 'Skill 已保存，兼容性问题需要处理。', 'skill-saved');
  }
  function rememberAction(action: SkillPendingAction | null) {
    if (!saveSkillAction(accountId, action, storage)) { setIssue('原操作记录未能可靠保存，未发送新请求。请恢复浏览器存储后重试。'); return false; }
    setPendingAction(action); setActionLatest(null); return true;
  }
  function finishAction(action: SkillPendingAction, detail?: AgentSkillDetail) {
    if (!rememberAction(null)) { setOperationIssue('已收到成功回执，但本机记录未清除。请继续核对原请求。'); return; }
    setOperationOpen(false); setDeleting(null); setOperationIssue(''); setRevision(value => value + 1);
    const cached = snapshot.current.draft;
    if (action.kind === 'delete' && cached?.base?.id === action.item.id && !cached.changed && !cached.pending && clearSkillDraft(accountId, storage)) {
      setDraft(null); setEditorOpen(false);
    }
    if (action.kind === 'copy' && detail) { updateDraft(createSkillDraft(detail)); setReadOnly(false); setEditorOpen(true); message.success('已复制为我的 Skill。', 'skill-copy'); }
    else message.success(action.kind === 'delete' ? 'Skill 已从当前技能库移除。' : 'Skill 调用设置已保存。', 'skill-action-saved');
  }
  async function execute(action: SkillPendingAction, reconcile = false) {
    if (lock.current || busy) return;
    lock.current = true; setBusy(true); setOperationIssue(''); setIssue('');
    try {
      if (reconcile && action.kind !== 'copy') {
        let latest: AgentSkillDetail;
        try { latest = await agentsApi.skill(action.item.id); }
        catch (cause) {
          if (cause instanceof ApiError && cause.status === 404 && action.kind === 'delete') { if (live.current) finishAction(action); return; }
          throw cause;
        }
        if (!live.current) return;
        if (action.kind === 'settings' && latest.enabled === action.enabled && latest.invocation_mode === action.invocation_mode) { finishAction(action, latest); return; }
        if (action.kind === 'settings' ? latest.settings_version !== action.item.settings_version : latest.row_version !== action.item.row_version) {
          setActionLatest(latest); setOperationIssue('当前保存状态已经变化。请核对后明确选择是否按此版本继续。'); return;
        }
      }
      const detail = action.kind === 'copy' ? await agentsApi.copySkill(action.item.id, action.item.content_version, action.key)
        : action.kind === 'settings' ? await agentsApi.updateSkillSettings(action.item.id, { row_version: action.item.settings_version,
          enabled: action.enabled, invocation_mode: action.invocation_mode })
        : await agentsApi.deleteSkill(action.item.id, action.item.row_version!);
      if (live.current) finishAction(action, detail || undefined);
    } catch (cause) {
      if (!live.current) return;
      const unknown = !(cause instanceof ApiError) || !cause.status || cause.status === 408 || cause.status >= 500;
      const conflict = cause instanceof ApiError && cause.status === 409;
      if (!unknown && !conflict || conflict && action.kind === 'copy') rememberAction(null);
      setOperationIssue(unknown ? '操作结果待核对，原请求已保留。'
        : conflict ? '版本已变化，原选择已保留，请读取当前状态后核对。' : `操作未完成：${errorMessage(cause)}`);
      setOperationOpen(true); setDeleting(null);
    } finally { lock.current = false; if (live.current) setBusy(false); }
  }
  function startAction(action: SkillPendingAction) {
    if (lock.current || busy || pendingAction || actionError) return;
    if (action.kind === 'copy' && !canOpenDraft()) return;
    if (rememberAction(action)) void execute(action);
  }
  async function reapplyAction() {
    if (!pendingAction || !actionLatest || busy) return;
    if (!await confirmAction(pendingAction.kind === 'delete' ? `已核对“${actionLatest.name}”的最新内容，仍要删除？当前已加载任务继续使用原版本。`
      : `将“${actionLatest.name}”改为${pendingAction.kind === 'settings' && pendingAction.enabled ? '启用' : '停用'}，并使用${pendingAction.kind === 'settings' && pendingAction.invocation_mode === 'auto' ? '按需自动' : '仅手动'}？`,
    { title: '核对最新 Skill 状态', confirmText: '按此版本继续', danger: pendingAction.kind === 'delete' })) return;
    const action = { ...pendingAction, item: actionLatest };
    if (rememberAction(action)) void execute(action);
  }
  async function exportItem(item: AgentSkill) {
    if (busy || lock.current) return;
    lock.current = true; setBusy(true);
    try { const file = await agentsApi.exportSkill(item.id, item.content_version); if (live.current) downloadSkillText(`${item.slug}.zip`, file); }
    catch (cause) { if (live.current) setIssue(`导出失败：${errorMessage(cause)}`); }
    finally { lock.current = false; if (live.current) setBusy(false); }
  }
  async function discardDraft() {
    if (draft?.pending || busy || !await confirmAction('放弃尚未保存的 Skill 草稿？请先下载需要保留的内容。', { title: '放弃 Skill 草稿', confirmText: '放弃草稿' })) return;
    if (clearSkillDraft(accountId, storage)) { setDraft(null); setDraftError(''); setEditorOpen(false); }
    else setIssue('草稿记录未能清除，请恢复浏览器存储后重试。');
  }
  function resetFilters() { setQuery(''); setSearch(''); setEnabled('all'); setInvocation('all'); setMode('all'); setOffset(0); }
  const filtered = !!search || enabled !== 'all' || invocation !== 'all' || mode !== 'all';
  const disabled = busy || !!pendingAction || !!actionError;
  return <>
    <div className="skill-status-entry"><StudioFeedbackIndicator/>{pendingAction && <Button size="small" disabled={busy} onClick={() => setOperationOpen(true)}>核对待处理操作</Button>}
      {draft && !editorOpen && <Button size="small" disabled={busy} onClick={() => setEditorOpen(true)}>恢复 Skill 草稿</Button>}</div>
    {issue && <StudioFeedback type="error" message={issue} noticeKey="skill-manager-operation" persistent actions={<Button onClick={() => setRevision(value => value + 1)}>重新读取列表</Button>}/>}
    {(draftError || actionError) && !editorOpen && <StudioFeedback type="warning" message={draftError || actionError} noticeKey="skill-manager-storage" persistent
      actions={<><Button onClick={() => { try { downloadSkillText('skill-recovery.json', actionError ? rawSkillAction(accountId, storage) : rawSkillDraft(accountId, storage)); } catch (cause) { setIssue(errorMessage(cause)); } }}>下载原记录</Button>
        <Button disabled={busy || !!pendingAction || !!draft?.pending} onClick={async () => {
          if (!await confirmAction('请先下载并核对原记录。明确清除会放弃无法读取的草稿或操作身份，确定继续？', { title: '清除损坏记录', confirmText: '明确清除' })) return;
          if (draftError && clearSkillDraft(accountId, storage)) setDraftError('');
          if (actionError && saveSkillAction(accountId, null, storage)) setActionError('');
        }}>明确清除原记录</Button></>}/>}
    {editorOpen && draft ? <SkillEditor key={draft.key} draft={draft} readOnly={readOnly} storageIssue={draftError} onDraft={updateDraft}
      onBusy={setBusy} onBack={() => { setEditorOpen(false); setBusy(false); }} onSaved={saved} onEdit={() => setReadOnly(false)}
      onCopy={() => startAction({ kind: 'copy', item: draft.base!, key: crypto.randomUUID() })}/>
      : <>
        <div className="skill-library-heading"><div><h2>Skill 管理</h2><p>维护可复用的创作方法，控制它们何时参与任务。</p></div>
          <div className="skill-actions"><Button icon={<Upload size={15}/>} disabled={disabled} onClick={() => { if (canOpenDraft()) setImportOpen(true); }}>导入 Skill</Button>
            <Button type="primary" icon={<Plus size={16}/>} disabled={disabled} onClick={() => newDraft(createSkillDraft())}>新建 Skill</Button></div></div>
        <div className="skill-library-controls"><Segmented aria-label="Skill 来源" value={source} disabled={busy} options={[{ value: 'personal', label: '我的 Skill' }, { value: 'builtin', label: '内置 Skill' }]}
          onChange={value => { setSource(value as typeof source); setOffset(0); }}/>
          <form className="skill-search" role="search" onSubmit={event => { event.preventDefault(); setSearch(query.trim()); setOffset(0); }}>
            <Search size={16}/><input aria-label="搜索 Skill" value={query} maxLength={200} placeholder="搜索名称、用途或标签" onChange={event => setQuery(event.target.value)}/><Button htmlType="submit" disabled={busy}>搜索</Button></form>
        </div>
        <div className="skill-filters"><label><span className="sr-only">Skill 启用状态</span><select aria-label="Skill 启用状态" value={enabled} disabled={busy} onChange={event => { setEnabled(event.target.value); setOffset(0); }}><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option></select></label>
          <label><span className="sr-only">Skill 调用筛选</span><select aria-label="Skill 调用筛选" value={invocation} disabled={busy} onChange={event => { setInvocation(event.target.value); setOffset(0); }}><option value="all">全部调用方式</option><option value="auto">按需自动</option><option value="manual">仅手动</option></select></label>
          <label><span className="sr-only">Skill 模式筛选</span><select aria-label="Skill 模式筛选" value={mode} disabled={busy} onChange={event => { setMode(event.target.value); setOffset(0); }}><option value="all">全部创作模式</option><option value="standard">标准创作</option><option value="infinite_canvas">无限画布</option></select></label>
          {filtered && <Button type="text" disabled={busy} onClick={resetFilters}>清除筛选</Button>}</div>
        <ListToolbar count={`共 ${total} 个${source === 'builtin' ? '内置' : '个人'} Skill`} actions={draft && <Button disabled={busy} onClick={() => void discardDraft()}>放弃草稿</Button>}/>
        {loading ? <div className="studio-empty" role="status" aria-label="正在读取 Skill 目录"><Skeleton active paragraph={{ rows: 4 }}/></div>
          : listError ? <div className="studio-empty" role="alert"><p>{listError}</p><Button onClick={() => setRevision(value => value + 1)}>重新读取 Skill</Button></div>
          : !items.length ? <div className="studio-empty skill-library-empty"><BookOpen size={26}/><h3>{filtered ? '没有匹配的 Skill' : source === 'personal' ? '把常用创作方法存为 Skill' : '暂无内置 Skill'}</h3>
            <p>{filtered ? '调整名称、状态或调用方式再试。' : source === 'personal' ? '写一份主指令，或导入已有 Markdown 与资料包。' : '稍后重新读取目录。'}</p>
            {filtered ? <Button onClick={resetFilters}>清除筛选</Button> : source === 'personal' && <Button disabled={disabled} onClick={() => newDraft(createSkillDraft())}>新建 Skill</Button>}</div>
          : <div className="skill-table-wrap"><table className="skill-table"><caption className="sr-only">{source === 'personal' ? '我的' : '内置'} Skill 列表</caption>
            <thead><tr><th scope="col">名称与用途</th><th scope="col">适用模式</th><th scope="col">启用状态</th><th scope="col">调用方式</th><th scope="col">版本</th><th scope="col">操作</th></tr></thead>
            <tbody>{items.map(item => <tr key={item.id}><td data-label="名称与用途"><strong>{item.name}</strong><p>{item.description}</p>
              {!item.compatible && <span className="skill-compatibility">需要处理：{item.issues.join('；')}</span>}
              <small>v{item.content_version} · {item.resource_count ? `${item.resource_count} 份资料 · ` : ''}{item.updated_at ? `更新于 ${new Date(item.updated_at).toLocaleDateString('zh-CN')}` : '系统内置'}</small></td>
              <td data-label="适用模式">{item.modes.map(value => value === 'standard' ? '标准创作' : '无限画布').join(' / ')}</td>
              <td data-label="启用状态"><Switch aria-label={`启用 ${item.name}`} checked={item.enabled} disabled={disabled || !item.compatible && !item.enabled}
                onChange={value => startAction({ kind: 'settings', item, enabled: value, invocation_mode: item.invocation_mode })}/><span className="skill-switch-label">{item.enabled ? '已启用' : '已停用'}</span></td>
              <td data-label="调用方式"><select aria-label={`调用方式 ${item.name}`} disabled={disabled} value={item.invocation_mode}
                onChange={event => startAction({ kind: 'settings', item, enabled: item.enabled, invocation_mode: event.target.value as 'auto' | 'manual' })}><option value="auto">按需自动</option><option value="manual">仅手动</option></select></td>
              <td data-label="版本" className="skill-version">v{item.content_version}</td><td data-label="操作"><div className="skill-actions"><Button disabled={disabled} onClick={() => void openItem(item, !item.builtin)}>{item.builtin ? '查看' : '编辑'}</Button>
                <Dropdown trigger={['click']} menu={{ items: [{ key: 'view', label: '查看内容' }, { key: 'copy', label: item.builtin ? '复制为我的 Skill' : '复制 Skill' }, { key: 'export', label: '导出 ZIP' },
                  ...(!item.builtin ? [{ key: 'delete', label: '删除 Skill', danger: true }] : [])], onClick: ({ key }) => {
                  if (key === 'view') void openItem(item); else if (key === 'copy') startAction({ kind: 'copy', item, key: crypto.randomUUID() });
                  else if (key === 'export') void exportItem(item); else setDeleting(item);
                } }}><Button aria-label={`更多操作：${item.name}`} icon={<Ellipsis size={16}/>} disabled={disabled}/></Dropdown></div></td></tr>)}</tbody></table></div>}
        <nav className="config-pagination" aria-label="Skill 分页"><span>第 {Math.floor(offset / 50) + 1} / {Math.max(1, Math.ceil(total / 50))} 页</span>
          <Button disabled={loading || busy || !offset} onClick={() => setOffset(value => Math.max(0, value - 50))}>上一页</Button>
          <Button disabled={loading || busy || offset + 50 >= total} onClick={() => setOffset(value => value + 50)}>下一页</Button></nav>
        <p className="skill-lifecycle-note">停用或删除会阻止后续加载；当前已加载的任务继续使用原版本，停止任务需在原会话操作。</p>
      </>}
    {importOpen && <SkillImportDialog onClose={() => setImportOpen(false)} onImport={content => newDraft(createSkillDraft(undefined, content))}/>}
    {deleting && <Dialog title="删除 Skill" canClose={!busy} onClose={() => setDeleting(null)}><p>删除“{deleting.name}”？后续任务不可加载，当前已加载的任务继续使用原版本。历史记录仍会保留。</p>
      <div className="dialog-actions"><Button disabled={busy} onClick={() => setDeleting(null)}>取消</Button><Button danger type="primary" loading={busy} onClick={() => startAction({ kind: 'delete', item: deleting })}>删除 Skill</Button></div></Dialog>}
    {operationOpen && <Dialog title="核对 Skill 操作" canClose={!busy} onClose={() => setOperationOpen(false)}>
      <p role="status">{operationIssue}</p>{pendingAction && <><p>{pendingAction.item.name} · {pendingAction.kind === 'copy' ? `复制内容版本 ${pendingAction.item.content_version}` : pendingAction.kind === 'delete' ? '删除本人条目' : `${pendingAction.enabled ? '启用' : '停用'} / ${pendingAction.invocation_mode === 'auto' ? '按需自动' : '仅手动'}`}</p>
        <details><summary>原操作详情</summary><pre>{JSON.stringify(pendingAction, null, 2)}</pre></details>
        {actionLatest && <p>当前服务端：{actionLatest.name} · 内容 v{actionLatest.content_version} · {actionLatest.enabled ? '已启用' : '已停用'} · {actionLatest.invocation_mode === 'auto' ? '按需自动' : '仅手动'}</p>}</>}
      <div className="dialog-actions"><Button disabled={busy} onClick={() => setOperationOpen(false)}>稍后处理</Button>{pendingAction && (actionLatest
        ? <Button type="primary" aria-label="按当前版本继续" loading={busy} onClick={() => void reapplyAction()}>按当前版本继续</Button>
        : <Button type="primary" aria-label={pendingAction.kind === 'copy' ? '使用原请求核对复制' : '读取当前状态并核对'} loading={busy} onClick={() => void execute(pendingAction, true)}>{pendingAction.kind === 'copy' ? '使用原请求核对复制' : '读取当前状态并核对'}</Button>)}</div>
    </Dialog>}
  </>;
}
