import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Input, Pagination, Segmented, Skeleton } from 'antd';
import { Import, ListFilter, Plus } from 'lucide-react';
import { assetLibraries, type AssetKind, type AssetScope, type LibraryAssetRead } from '../../api/modules/assets';
import type { CreationDraftValue } from '../../api/modules/creation-drafts';
import { errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { confirmAction } from '../../components/ui/confirm';
import { useAuth } from '../auth/AuthSession';
import { EpisodeEditorSlot, useEpisodeCreationControls } from '../projects/EpisodeCreationWorkspace';
import { creationScope, saveRecovery } from '../projects/creation-recovery';
import type { NavigationBarrier } from '../projects/writing-navigation';
import { useAssetLibrary } from './useAssetLibrary';
import { StandardEpisodeAssetCard } from './EpisodeAssetCard';
import { EpisodeAssetEditor, LocalAssetEditor } from './EpisodeAssetEditor';
import { assetCreationAttempt, assetCreationUnknown, assetDraftHasInput, blankAssetDraft, localAssetDraftKey, mergeCreatedAsset, readLocalAssetDrafts, type LocalAssetDraft } from './episode-asset-drafts';
import './episode-assets.css';

const labels = { all: '全部', character: '角色', scene: '场景', prop: '道具' };
type EpisodeScope = Extract<AssetScope, { kind: 'episode' }>;

export function EpisodeAssetWorkspace({ scope, title, readOnly = false, importFrom, shareTo, toolbar, refreshToken = 0, registerBarrier }: {
  scope: EpisodeScope; title: string; readOnly?: boolean; importFrom?: AssetScope; shareTo?: AssetScope;
  toolbar?: ReactNode; refreshToken?: number; registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  const auth = useAuth();
  const actor = auth.user?.id ?? (auth.enabled ? 'signed-out' : 'anonymous');
  return <ScopedEpisodeAssets key={`${actor}:${scope.projectId}:${scope.episodeId}`} {...{ scope, title, readOnly, importFrom, shareTo, toolbar, refreshToken, registerBarrier, actor }}/>;
}

function ScopedEpisodeAssets({ scope, title, readOnly, importFrom, shareTo, toolbar, refreshToken, registerBarrier, actor }: {
  scope: EpisodeScope; title: string; readOnly: boolean; importFrom?: AssetScope; shareTo?: AssetScope;
  toolbar?: ReactNode; refreshToken: number; registerBarrier?: (barrier: NavigationBarrier | null) => void; actor: string;
}) {
  const creation = useEpisodeCreationControls();
  const [kind, setKind] = useState<AssetKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const { items, total, loading, error, refresh } = useAssetLibrary(scope, kind, query, offset);
  const recoveryKey = localAssetDraftKey(actor, scope.projectId, scope.episodeId);
  const [recoveryError, setRecoveryError] = useState('');
  const [drafts, setDrafts] = useState<LocalAssetDraft[]>([]);
  const draftsRef = useRef(drafts); draftsRef.current = drafts;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selection = useRef(selectedId); selection.current = selectedId;
  const [detail, setDetail] = useState<LibraryAssetRead | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [overrides, setOverrides] = useState<Record<string, LibraryAssetRead>>({});
  const [privateInitial, setPrivateInitial] = useState<Record<string, CreationDraftValue>>({});
  const [notice, setNotice] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [imports, setImports] = useState<LibraryAssetRead[]>([]);
  const [importQuery, setImportQuery] = useState('');
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState('');
  const [linking, setLinking] = useState<string | null>(null);
  const entries = useRef(new Map<string, HTMLButtonElement>());
  const requests = useRef<{ detail?: AbortController; import?: AbortController }>({});
  const creating = useRef(new Set<string>());
  const alive = useRef(true);
  const switching = useRef(false);
  const requestedSubject = useRef<string | null>(null);
  const editorBarrier = useRef<NavigationBarrier | null>(null);
  const registerEditor = useCallback((barrier: NavigationBarrier | null) => { editorBarrier.current = barrier; }, []);
  const [uploadBusy, setUploadBusy] = useState(false);

  useEffect(() => {
    alive.current = true;
    try { const recovered = readLocalAssetDrafts(recoveryKey); setDrafts(recovered); draftsRef.current = recovered; }
    catch { setRecoveryError('本机素材草稿无法读取。请下载备份后明确清除恢复记录。'); }
    return () => { alive.current = false; requests.current.detail?.abort(); requests.current.import?.abort(); };
  }, [recoveryKey]);
  useEffect(() => { if (refreshToken) refresh(); }, [refreshToken, refresh]);
  useEffect(() => {
    registerBarrier?.({
      hasUnsettled: () => !!editorBarrier.current?.hasUnsettled() || draftsRef.current.some(assetDraftHasInput) || !!creating.current.size || !!recoveryError,
      flush: async () => {
        if (recoveryError || creating.current.size) return false;
        if (editorBarrier.current?.hasUnsettled() && !await editorBarrier.current.flush()) return false;
        if (draftsRef.current.some(assetDraftHasInput)) { setNotice('新素材仍是本机草稿，请补齐名称和类型后再离开。'); return false; }
        return true;
      },
    });
    return () => registerBarrier?.(null);
  }, [registerBarrier, recoveryError]);
  useEffect(() => {
    const status = creating.current.size ? 'saving' : drafts.some(assetDraftHasInput) ? 'local' : null;
    window.dispatchEvent(new CustomEvent('creation-save-state', { detail: { key: `asset-new:${recoveryKey}`, status, message: status === 'local' ? '本机草稿已保留' : '' } }));
    return () => { window.dispatchEvent(new CustomEvent('creation-save-state', { detail: { key: `asset-new:${recoveryKey}`, status: null } })); };
  }, [drafts, recoveryKey]);

  function keepDrafts(next: LocalAssetDraft[]) {
    draftsRef.current = next; setDrafts(next);
    try { if (next.length) localStorage.setItem(recoveryKey, JSON.stringify(next)); else localStorage.removeItem(recoveryKey); }
    catch { setRecoveryError('浏览器无法保留素材草稿，请保持本页打开并下载备份。'); }
  }
  function editDraft(id: string, patch: Partial<LocalAssetDraft>) {
    keepDrafts(draftsRef.current.map(draft => draft.id === id ? { ...draft, ...patch } : draft));
  }
  const localSelected = drafts.find(draft => draft.id === selectedId);
  const known = items.map(item => overrides[item.id] ?? item);

  const receiveArtwork = useCallback((asset: LibraryAssetRead) => {
    if (!alive.current) return;
    setOverrides(current => ({ ...current, [asset.id]: asset }));
    if (selection.current === asset.id) {
      creation.selectSubject?.({ type: 'asset', id: asset.id, label: `${labels[asset.kind]} · ${asset.name}`, revision: asset.row_version });
    }
  }, [creation.selectSubject]);

  async function selectAsset(id: string, seed?: LibraryAssetRead) {
    if (switching.current || id === selection.current) return;
    switching.current = true;
    try {
      if (editorBarrier.current?.hasUnsettled() && !await editorBarrier.current.flush()) return;
      requests.current.detail?.abort();
      selection.current = id; setSelectedId(id); setNotice(''); setDetailError('');
      creation.revealPanel?.();
      if (id.startsWith('draft:')) { setDetail(null); setDetailLoading(false); creation.selectSubject?.(null); return; }
      if (seed) creation.selectSubject?.({ type: 'asset', id, label: `${labels[seed.kind]} · ${seed.name}`, revision: seed.row_version });
      void readDetail(id, seed);
    } finally { switching.current = false; }
  }
  async function readDetail(id: string, seed?: LibraryAssetRead) {
    requests.current.detail?.abort();
    const controller = new AbortController(); requests.current.detail = controller;
    setDetail(null); setDetailLoading(true); setDetailError('');
    try {
      const remote = await assetLibraries.detail(id, controller.signal);
      if (controller.signal.aborted || selection.current !== id || !alive.current) return;
      const asset = { link_id: null, position: null, ...seed, ...remote };
      setDetail(asset); receiveArtwork(asset);
    } catch (cause) { if (!controller.signal.aborted && alive.current) setDetailError(errorMessage(cause)); }
    finally { if (!controller.signal.aborted && alive.current) setDetailLoading(false); }
  }
  useEffect(() => {
    const requested = creation.subject?.type === 'asset' ? creation.subject.id : null;
    const changed = requested !== requestedSubject.current;
    requestedSubject.current = requested;
    const activeId = selection.current;
    if (activeId && !activeId.startsWith('draft:') && requests.current.detail?.signal.aborted) {
      void readDetail(activeId, known.find(item => item.id === activeId));
      return;
    }
    if (changed && requested && requested !== selection.current) { void selectAsset(requested, known.find(item => item.id === requested)); return; }
    if (!selectedId && !loading && !error && !recoveryError) {
      if (draftsRef.current.length) void selectAsset(draftsRef.current.at(-1)!.id);
      else if (known[0]) void selectAsset(known[0].id, known[0]);
    }
  }, [creation.subject?.type, creation.subject?.id, selectedId, loading, items, error, recoveryError]);

  async function addDraft() {
    if (readOnly || recoveryError || switching.current) return;
    if (editorBarrier.current?.hasUnsettled() && !await editorBarrier.current.flush()) return;
    const draft = blankAssetDraft(kind);
    keepDrafts([...draftsRef.current, draft]); setQuery('');
    selection.current = draft.id; setSelectedId(draft.id); setDetail(null); setDetailError(''); setDetailLoading(false);
    creation.selectSubject?.(null); creation.revealPanel?.();
    requestAnimationFrame(() => entries.current.get(draft.id)?.scrollIntoView({ block: 'nearest' }));
  }
  async function createDraft(id: string) {
    const draft = draftsRef.current.find(item => item.id === id);
    if (!draft || creating.current.has(id) || readOnly || recoveryError) return false;
    if (selection.current === id && editorBarrier.current?.hasUnsettled()) return false;
    const attempt = assetCreationAttempt(draft);
    if (!attempt) return false;
    creating.current.add(id); editDraft(id, { attempt, status: 'saving', message: '正在保存到服务端…' });
    try {
      const saved = await assetLibraries.create(scope, attempt.body, attempt.key);
      if (!alive.current) return false;
      const current = draftsRef.current.find(item => item.id === id) ?? draft;
      const edited = mergeCreatedAsset(saved, current);
      if (current.generation) {
        saveRecovery(creationScope(`generation-draft:${scope.projectId}:${scope.episodeId}:${saved.id}:asset_image`), { row_version: null, value: current.generation });
        setPrivateInitial(value => ({ ...value, [saved.id]: current.generation! }));
      }
      setOverrides(value => ({ ...value, [saved.id]: edited }));
      // Seed a shared editing recovery for keystrokes entered after the create request.
      if (JSON.stringify(attempt.body) !== JSON.stringify({ kind: edited.kind, name: edited.name.trim(), label: edited.label, description: edited.description, prompt: edited.prompt, tags: edited.tags, scene_time: edited.scene_time })) {
        const { assetRecoveryKey, assetRecoveryStorage, assetFields } = await import('./asset-editing-session');
        assetRecoveryStorage(assetRecoveryKey(actor, scope.projectId, scope.episodeId, saved.id)).save({ base: saved, fields: assetFields(edited) });
      }
      keepDrafts(draftsRef.current.filter(item => item.id !== id));
      if (selection.current === id) { selection.current = saved.id; setSelectedId(saved.id); setDetail(saved); receiveArtwork(edited); }
      refresh(); return true;
    } catch (cause) {
      if (alive.current) editDraft(id, { status: assetCreationUnknown(cause) ? 'unknown' : 'error',
        message: assetCreationUnknown(cause) ? '创建受理结果待核对。草稿和原请求已保留，请核对原创建请求。' : `创建素材失败：${errorMessage(cause)}` });
      return false;
    } finally { creating.current.delete(id); }
  }
  useEffect(() => {
    if (!localSelected || localSelected.status !== 'local' || !localSelected.name.trim() || !localSelected.kind || uploadBusy) return;
    const timer = setTimeout(() => { void createDraft(localSelected.id); }, 1000);
    return () => clearTimeout(timer);
  }, [localSelected, readOnly, recoveryError, uploadBusy]);

  async function openImports() {
    if (!importFrom || readOnly) return;
    requests.current.import?.abort(); const controller = new AbortController(); requests.current.import = controller;
    setImportOpen(true); setImportLoading(true); setImportError(''); setImports([]); setImportQuery('');
    try {
      const source: LibraryAssetRead[] = []; const members = new Set<string>();
      for (const [target, sink] of [[importFrom, source], [scope, members]] as const) {
        let next = 0; let count = 1;
        while (next < count) {
          const page = await assetLibraries.list(target, { kind: kind === 'all' ? undefined : kind, offset: next, limit: 100 }, controller.signal);
          for (const item of page.items) { if (Array.isArray(sink)) sink.push(item); else sink.add(item.id); }
          next += page.items.length; count = page.total; if (!page.items.length) break;
        }
      }
      if (!controller.signal.aborted) setImports(source.filter(item => !members.has(item.id)));
    } catch (cause) { if (!controller.signal.aborted) setImportError(errorMessage(cause)); }
    finally { if (!controller.signal.aborted) setImportLoading(false); }
  }
  async function link(id: string) {
    if (linking) return; setLinking(id); setImportError('');
    try { const asset = await assetLibraries.link(scope, id); setImportOpen(false); refresh(); await selectAsset(asset.id, asset); }
    catch (cause) { setImportError(errorMessage(cause)); }
    finally { setLinking(null); }
  }
  async function removeDraft(id: string) {
    if (creating.current.has(id) || !await confirmAction('明确放弃这张本机素材草稿？')) return;
    keepDrafts(draftsRef.current.filter(item => item.id !== id));
    if (selection.current === id) { selection.current = null; setSelectedId(null); }
  }
  async function removeSaved(asset: LibraryAssetRead) {
    if (editorBarrier.current?.hasUnsettled() && !await editorBarrier.current.flush()) return;
    if (!await confirmAction(`从本集移除“${asset.name}”？其他库引用仍会保留。`)) return;
    try { await assetLibraries.unlink(scope, asset.id, (overrides[asset.id] ?? asset).row_version); selection.current = null; setSelectedId(null); setDetail(null); creation.selectSubject?.(null); refresh(); }
    catch (cause) { setNotice(errorMessage(cause)); }
  }

  const visibleDrafts = drafts.filter(item => (kind === 'all' || item.kind === kind) && (!query.trim() || `${item.name}${item.description}`.includes(query.trim())));
  const editor = localSelected ? <LocalAssetEditor draft={localSelected} readOnly={readOnly || !!recoveryError} registerBarrier={registerEditor} onUploadBusyChange={setUploadBusy} onChange={patch => editDraft(localSelected.id, patch)} onRetry={() => void createDraft(localSelected.id)} onRemove={() => void removeDraft(localSelected.id)}/>
    : detail && detail.id === selectedId ? <EpisodeAssetEditor key={detail.id} asset={detail} scope={scope} actor={actor} readOnly={readOnly} initialGeneration={privateInitial[detail.id]}
      registerBarrier={registerEditor} onArtwork={receiveArtwork} onRemove={() => void removeSaved(overrides[detail.id] ?? detail)} onShare={shareTo ? async () => { try { await assetLibraries.link(shareTo, detail.id); setNotice('素材已共享到项目库。'); } catch (cause) { setNotice(errorMessage(cause)); } } : undefined}/>
    : <section className="standard-object-panel standard-asset-empty" aria-label="素材工作区">{detailLoading ? <div role="status"><Skeleton active paragraph={{ rows: 5 }}/><p>正在读取最新素材…</p></div> : detailError ? <Alert type="error" showIcon message={detailError} action={<Button onClick={() => selectedId && void readDetail(selectedId)}>重新读取素材</Button>}/> : <div className="studio-empty"><h3>选择一个素材</h3><p>从左侧选择素材，或新增一张空白卡片。</p></div>}</section>;

  return <section className="standard-assets-list" aria-label={title}>
    <header className="standard-assets-heading"><h2>素材 <small>{total + drafts.length}</small></h2><div className="standard-assets-tools">{!readOnly && <Button type="text" aria-label="新增素材" title="新增素材" icon={<Plus size={17}/>} disabled={!!recoveryError} onClick={() => void addDraft()}/>}<span className="standard-extraction-tool" title="从剧本提取素材"><span className="standard-extraction-icon" aria-hidden="true"><ListFilter size={17}/></span>{toolbar}</span>{importFrom && !readOnly && <Button type="text" aria-label="导入素材" title="从项目素材库导入" icon={<Import size={17}/>} onClick={() => void openImports()}/>}</div></header>
    <div className="standard-assets-filters"><Input.Search value={query} onChange={event => { setQuery(event.target.value); setOffset(0); }} aria-label="搜索素材" placeholder="搜索素材名称" allowClear/><Segmented block aria-label="素材类别" value={kind} options={Object.entries(labels).map(([value, label]) => ({ value, label }))} onChange={value => { setKind(value as AssetKind | 'all'); setOffset(0); }}/></div>
    {notice && <Alert type="warning" message={notice} closable onClose={() => setNotice('')}/>}
    {recoveryError && <Alert type="error" message={recoveryError} action={<><Button onClick={() => {
      let content = JSON.stringify(draftsRef.current, null, 2);
      try { content = localStorage.getItem(recoveryKey) ?? content; } catch { /* The current in-memory draft can still be exported. */ }
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
      const link = document.createElement('a'); link.href = url; link.download = `episode-${scope.episodeId}-asset-drafts.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }}>下载本机恢复记录</Button><Button onClick={async () => {
      if (!await confirmAction('明确清除本机素材恢复记录？此操作不会删除服务端素材。')) return;
      try { localStorage.removeItem(recoveryKey); setRecoveryError(''); keepDrafts([]); } catch { setRecoveryError('浏览器存储仍不可用，请下载草稿备份。'); }
    }}>清除本机恢复记录</Button></>}/>}
    <div className="standard-assets-scroll">{error && <Alert type="error" showIcon message={error} action={<Button onClick={refresh}>重新加载素材</Button>}/>}
      {loading && !known.length ? <Skeleton active paragraph={{ rows: 6 }}/> : known.map(item => <StandardEpisodeAssetCard key={item.id} asset={item} selected={selectedId === item.id} disabled={false} entryRef={node => { if (node) entries.current.set(item.id, node); else entries.current.delete(item.id); }} onOpen={() => void selectAsset(item.id, item)}/>)}
      {visibleDrafts.map(item => <StandardEpisodeAssetCard key={item.id} asset={item} selected={selectedId === item.id} disabled={false} entryRef={node => { if (node) entries.current.set(item.id, node); else entries.current.delete(item.id); }} onOpen={() => void selectAsset(item.id)}/>)}
      {!loading && !error && !known.length && !visibleDrafts.length && <div className="studio-empty"><h3>{query ? '没有匹配素材' : '暂无素材'}</h3>{!readOnly && <Button onClick={() => void addDraft()}>新增素材</Button>}</div>}
      <Pagination size="small" current={Math.floor(offset / 20) + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={page => setOffset((page - 1) * 20)}/>
    </div>
    <EpisodeEditorSlot stage="assets">{editor}</EpisodeEditorSlot>
    {importOpen && <Dialog title="从项目库导入素材" className="asset-import-dialog" canClose={!linking} onClose={() => { requests.current.import?.abort(); setImportOpen(false); }}>
      <div className="asset-import-search"><Input.Search aria-label="搜索可添加素材" value={importQuery} placeholder="搜索名称或描述" allowClear onChange={event => setImportQuery(event.target.value)}/></div>
      <div className="asset-import-body">{importError && <Alert type="error" message={importError} action={<Button onClick={() => void openImports()}>重新读取</Button>}/>}{importLoading ? <Skeleton active paragraph={{ rows: 3 }}/> : imports.filter(item => `${item.name}${item.description}`.includes(importQuery.trim())).map(item => <div className="resource-import-row" key={item.id}><div><strong>{item.name}</strong><p>{item.description}</p></div><Button disabled={!!linking} loading={linking === item.id} onClick={() => void link(item.id)}>添加到本集</Button></div>)}{!importLoading && !importError && !imports.length && <p>项目库暂无可添加的素材。</p>}</div>
      <div className="production-dialog-footer"><span>同一项目引用同一素材，作品修改会同步。</span><Button disabled={!!linking} onClick={() => setImportOpen(false)}>关闭</Button></div>
    </Dialog>}
  </section>;
}
