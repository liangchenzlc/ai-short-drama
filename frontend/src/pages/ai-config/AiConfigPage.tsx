import { Button, Skeleton } from 'antd';
import { Icon } from '../../components/ui/Icon';
import { useEffect, useRef, useState } from 'react';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import { ApiError, errorMessage, isCancelled } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { configTabs, useAiConfigSession } from '../../features/ai-config/AiConfigSession';
import { ConfigForm } from '../../features/ai-config/ConfigForm';
import { ConfigTable } from '../../features/ai-config/ConfigTable';
import { serviceLabels, type AiConfig, type ConfigDraft, type ServiceType } from '../../features/ai-config/config-model';

const PAGE_SIZE = 20;
export function AiConfigPage() {
  const { tab, setTab } = useAiConfigSession();
  const [items, setItems] = useState<AiConfig[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formRevision, setFormRevision] = useState(0);
  const [editing, setEditing] = useState<AiConfig | null>(null);
  const [deleting, setDeleting] = useState<AiConfig | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [deleteConflict, setDeleteConflict] = useState(false);
  const listRequest = useRef<AbortController | null>(null);
  const refresh = () => { listRequest.current?.abort(); setLoading(true); setRevision((value) => value + 1); };

  useEffect(() => {
    const controller = new AbortController();
    listRequest.current = controller;
    setLoading(true); setListError('');
    aiModelConfigs.list(tab, offset, PAGE_SIZE, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (!result.items.length && offset > 0) {
        setOffset(Math.max(0, Math.ceil(result.total / PAGE_SIZE) - 1) * PAGE_SIZE); return;
      }
      setItems(result.items); setTotal(result.total); setLoading(false);
    }).catch((cause) => {
      if (controller.signal.aborted || isCancelled(cause)) return;
      setListError(errorMessage(cause)); setLoading(false);
    });
    return () => controller.abort();
  }, [tab, offset, revision]);

  function selectTab(kind: ServiceType) {
    if (kind === tab) return;
    listRequest.current?.abort();
    setTab(kind); setOffset(0); setItems([]); setTotal(0); setLoading(true);
    setNotice(''); setActionError('');
  }
  async function save(draft: ConfigDraft) {
    if (editing) await aiModelConfigs.update(editing.id, editing.rowVersion, draft);
    else await aiModelConfigs.create(draft);
    setFormOpen(false); setEditing(null); setNotice('配置已保存。'); setActionError(''); refresh();
  }
  async function edit(item: AiConfig) {
    if (busyId) return;
    setBusyId(item.id); setActionError(''); setNotice('');
    try { setEditing(await aiModelConfigs.get(item.id)); setFormOpen(true); }
    catch (cause) { setActionError(errorMessage(cause)); }
    finally { setBusyId(null); }
  }
  async function setDefault(item: AiConfig) {
    if (busyId) return;
    setBusyId(item.id); setActionError(''); setNotice('');
    try { await aiModelConfigs.setDefault(item.id, item.rowVersion); setNotice('默认配置已更新。'); refresh(); }
    catch (cause) { setActionError(errorMessage(cause)); }
    finally { setBusyId(null); }
  }
  async function remove() {
    if (!deleting || busyId || deleteConflict) return;
    setBusyId(deleting.id); setDeleteError('');
    try {
      await aiModelConfigs.remove(deleting.id, deleting.rowVersion);
      setDeleting(null); setNotice('配置已删除。'); setActionError(''); refresh();
    } catch (cause) {
      setDeleteError(errorMessage(cause));
      setDeleteConflict(cause instanceof ApiError && (cause.status === 409 || cause.status === 404));
    } finally { setBusyId(null); }
  }

  return <section className="studio-page ai-config-page" aria-labelledby="ai-title">
    <div className="studio-page-head"><div><h1 id="ai-title">AI 配置</h1><p>连接创作模型，为文字、图片和视频分别设置默认选择。</p></div><Button type="primary" icon={<Icon name="plus" size={16}/>} disabled={!!busyId} onClick={() => { setEditing(null); setFormOpen(true); }}>添加{serviceLabels[tab]}</Button></div>
    <div className="preview-note" role="note">为每类选择一个默认模型，创作时自动选用。保存配置不会发起生成。</div>
    <div className="studio-tabs" role="tablist" aria-label="AI 模型类别">
      {configTabs.map((kind, index) => <button key={kind} id={`model-tab-${kind}`} role="tab" aria-selected={tab === kind}
        aria-controls={`model-panel-${kind}`} tabIndex={tab === kind ? 0 : -1} className={tab === kind ? 'active' : ''}
        disabled={!!busyId} onClick={() => selectTab(kind)} onKeyDown={(event) => {
          const next = event.key === 'ArrowRight' ? (index + 1) % configTabs.length
            : event.key === 'ArrowLeft' ? (index + configTabs.length - 1) % configTabs.length
            : event.key === 'Home' ? 0 : event.key === 'End' ? configTabs.length - 1 : -1;
          if (next < 0) return;
          event.preventDefault(); selectTab(configTabs[next]); document.getElementById(`model-tab-${configTabs[next]}`)?.focus();
        }}>{serviceLabels[kind]}</button>)}
    </div>
    {notice && <p role="status" className="config-feedback">{notice}</p>}
    {actionError && <div role="alert" className="form-error config-feedback">{actionError}<button onClick={() => { setActionError(''); refresh(); }} disabled={loading || !!busyId}>重新加载列表</button></div>}
    {configTabs.map((kind) => <div key={kind} id={`model-panel-${kind}`} role="tabpanel" aria-labelledby={`model-tab-${kind}`} hidden={tab !== kind}>
      {tab === kind && <>
        <div className="studio-toolbar">
          <span className="config-list-count">共 {total} 个{serviceLabels[kind]}</span><Button disabled={!!busyId} loading={loading} onClick={refresh}>刷新列表</Button>
        </div>
        {loading ? <div className="studio-empty" role="status" aria-live="polite"><Skeleton title paragraph={{ rows: 3 }}/></div>
          : listError ? <div className="studio-empty" role="alert"><p>{listError}</p><button onClick={refresh}>重新加载</button></div>
          : <><ConfigTable items={items} serviceType={kind} busyId={busyId} onEdit={edit} onDefault={setDefault}
            onDelete={(item) => { setDeleting(item); setDeleteError(''); setDeleteConflict(false); }} />
            <nav className="config-pagination" aria-label="配置分页">
              <span>共 {total} 条 · 第 {Math.floor(offset / PAGE_SIZE) + 1} / {Math.max(1, Math.ceil(total / PAGE_SIZE))} 页</span>
              <button disabled={offset === 0 || !!busyId} onClick={() => { setLoading(true); setOffset((value) => Math.max(0, value - PAGE_SIZE)); }}>上一页</button>
              <button disabled={offset + PAGE_SIZE >= total || !!busyId} onClick={() => { setLoading(true); setOffset((value) => value + PAGE_SIZE); }}>下一页</button>
            </nav></>}
      </>}
    </div>)}
    {formOpen && <ConfigForm key={`${editing?.id ?? 'new'}-${formRevision}`} existing={editing} serviceType={tab}
      onClose={() => { setFormOpen(false); setEditing(null); }} onSave={save}
      onReload={async () => {
        if (!editing) return;
        const latest = await aiModelConfigs.get(editing.id);
        setEditing(latest); setFormRevision((value) => value + 1); refresh();
      }} />}
    {deleting && <Dialog title="删除配置" onClose={() => setDeleting(null)} canClose={!busyId}>
      <p>确定删除「{deleting.name}」吗？{deleting.isDefault ? '删除后，此类模型将没有默认配置。' : '删除后将从列表移除。'}</p>
      {deleteError && <p role="alert" className="form-error">{deleteError}</p>}
      <div className="dialog-actions"><button onClick={() => setDeleting(null)} disabled={!!busyId}>取消</button>
        {deleteConflict ? <button onClick={() => { setDeleting(null); refresh(); }}>重新加载列表</button>
          : <button className="danger-button" onClick={remove} disabled={!!busyId}>{busyId ? '删除中…' : '确认删除'}</button>}
      </div>
    </Dialog>}
  </section>;
}
