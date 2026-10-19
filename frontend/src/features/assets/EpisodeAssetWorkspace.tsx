import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { Alert, Button, Input, Pagination, Skeleton, Spin } from 'antd';
import { Image as ImageIcon, Plus, Search, Sparkles, TriangleAlert, Upload } from 'lucide-react';
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
import { blankAssetDraft, localAssetDraftKey, type LocalAssetDraft } from './episode-asset-drafts';
import { LocalAssetSession } from './local-asset-session';
import { AssetEditingSession, assetRecoveryKey, assetRecoveryStorage, assetFields } from './asset-editing-session';
import { releaseStandardSession, retainStandardSession } from '../projects/standard-session-registry';
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
  const { items, total, loading, error, refresh: refreshItems } = useAssetLibrary(scope, kind, query, offset);
  const [categoryCounts, setCategoryCounts] = useState<Record<AssetKind | 'all', number> | null>(null);
  const [countError, setCountError] = useState('');
  const [countRevision, setCountRevision] = useState(0);
  const refresh = useCallback(() => { refreshItems(); setCountRevision(value => value + 1); }, [refreshItems]);
  useEffect(() => {
    const controller = new AbortController();
    const kinds = ['all', 'character', 'scene', 'prop'] as const;
    setCountError('');
    Promise.all(kinds.map(async category => {
      const page = await assetLibraries.list(scope, { kind: category === 'all' ? undefined : category, limit: 1 }, controller.signal);
      return [category, page.total] as const;
    })).then(entries => {
      if (!controller.signal.aborted) setCategoryCounts(Object.fromEntries(entries) as Record<AssetKind | 'all', number>);
    }).catch(cause => {
      if (!controller.signal.aborted) setCountError(`读取素材分类数量失败：${errorMessage(cause)}`);
    });
    return () => controller.abort();
  }, [scope.projectId, scope.episodeId, countRevision]);
  const recoveryKey = localAssetDraftKey(actor, scope.projectId, scope.episodeId);
  const localResource = `asset-new:${scope.projectId}:${scope.episodeId}`;
  const localSession = useMemo(() => retainStandardSession(actor, localResource, () => new LocalAssetSession(recoveryKey, {
    create: (body, key) => assetLibraries.create(scope, body, key),
    preserveCreated: (saved, current) => {
      if (current.generation) saveRecovery(creationScope(`generation-draft:${scope.projectId}:${scope.episodeId}:${saved.id}:asset_image`, actor), { row_version: null, value: current.generation });
      const storage = assetRecoveryStorage(assetRecoveryKey(actor, scope.projectId, scope.episodeId, saved.id));
      if (JSON.stringify(assetFields(saved)) !== JSON.stringify(assetFields({ ...current, kind: current.kind ?? saved.kind }))) {
        storage.save({ base: saved, fields: assetFields({ ...current, kind: current.kind ?? saved.kind }) });
        const resource = `asset:${saved.id}`;
        retainStandardSession(actor, resource, () => new AssetEditingSession(saved, assetLibraries.update, storage, 1000, assetLibraries.detail), { label: `素材 · ${current.name}`, projectId: scope.projectId, episodeId: scope.episodeId });
        releaseStandardSession(actor, resource);
      }
    },
  }, readOnly), { label: '新素材', projectId: scope.projectId, episodeId: scope.episodeId }), [actor, localResource, recoveryKey, readOnly, scope.projectId, scope.episodeId]);
  const localState = useSyncExternalStore(localSession.subscribe, localSession.getSnapshot);
  const { drafts, recoveryError } = localState;
  useEffect(() => () => releaseStandardSession(actor, localResource), [localSession, actor, localResource]);
  const draftsRef = useRef(drafts); draftsRef.current = drafts;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selection = useRef(selectedId); selection.current = selectedId;
  const [detail, setDetail] = useState<LibraryAssetRead | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [overrides, setOverrides] = useState<Record<string, LibraryAssetRead>>({});
  const countedVersions = useRef(new Map<string, { kind: AssetKind; rowVersion: string }>());
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
  const alive = useRef(true);
  const switching = useRef(false);
  const requestedSubject = useRef<string | null>(null);
  const editorBarrier = useRef<NavigationBarrier | null>(null);
  const registerEditor = useCallback((barrier: NavigationBarrier | null) => { editorBarrier.current = barrier; }, []);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; requests.current.detail?.abort(); requests.current.import?.abort(); };
  }, [recoveryKey]);
  useEffect(() => { if (refreshToken) refresh(); }, [refreshToken, refresh]);
  useEffect(() => {
    registerBarrier?.({ hasUnsettled: () => !!editorBarrier.current?.hasUnsettled() || localSession.hasUnsettled(),
      hasMemoryOnlyChanges: () => localSession.hasMemoryOnlyChanges() || !!editorBarrier.current?.hasMemoryOnlyChanges?.(),
      flush: async () => { void editorBarrier.current?.flush(); return localSession.flush(); } });
    return () => registerBarrier?.(null);
  }, [registerBarrier, localSession]);
  function keepDrafts(next: LocalAssetDraft[]) { localSession.replace(next); draftsRef.current = next; }
  function editDraft(id: string, patch: Partial<LocalAssetDraft>) { localSession.edit(id, patch); draftsRef.current = localSession.getSnapshot().drafts; }
  const localSelected = drafts.find(draft => draft.id === selectedId);
  const known = items.map(item => overrides[item.id] ?? item);

  const receiveArtwork = useCallback((asset: LibraryAssetRead) => {
    if (!alive.current) return;
    setOverrides(current => ({ ...current, [asset.id]: asset }));
    const counted = countedVersions.current.get(asset.id);
    if (!counted || counted.rowVersion !== asset.row_version) {
      countedVersions.current.set(asset.id, { kind: asset.kind, rowVersion: asset.row_version });
      if (counted && counted.kind !== asset.kind) refresh();
    }
    if (selection.current === asset.id) {
      creation.selectSubject?.({ type: 'asset', id: asset.id, label: `${labels[asset.kind]} · ${asset.name}`, revision: asset.row_version });
    }
  }, [creation.selectSubject, refresh]);

  async function selectAsset(id: string, seed?: LibraryAssetRead) {
    if (switching.current || id === selection.current) return;
    switching.current = true;
    try {
      if (editorBarrier.current?.hasUnsettled()) void editorBarrier.current.flush();
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
    if (editorBarrier.current?.hasUnsettled()) void editorBarrier.current.flush();
    const draft = blankAssetDraft(kind);
    keepDrafts([...draftsRef.current, draft]); setQuery('');
    selection.current = draft.id; setSelectedId(draft.id); setDetail(null); setDetailError(''); setDetailLoading(false);
    creation.selectSubject?.(null); creation.revealPanel?.();
    requestAnimationFrame(() => entries.current.get(draft.id)?.scrollIntoView({ block: 'nearest' }));
  }
  const createdSeen = useRef(new Set<string>());
  useEffect(() => {
    for (const [id, result] of Object.entries(localState.created)) {
      if (createdSeen.current.has(id)) continue;
      createdSeen.current.add(id);
      if (result.generation) setPrivateInitial(value => ({ ...value, [result.asset.id]: result.generation! }));
      setOverrides(value => ({ ...value, [result.asset.id]: result.asset }));
      if (selection.current === id) { selection.current = result.asset.id; setSelectedId(result.asset.id); setDetail(result.asset); receiveArtwork(result.asset); }
      refresh();
    }
  }, [localState.created, receiveArtwork, refresh]);
  const createDraft = (id: string) => localSession.create(id);
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
    if (draftsRef.current.find(item => item.id === id)?.status === 'saving' || !await confirmAction('明确放弃这张本机素材草稿？')) return;
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
  const editor = localSelected ? <LocalAssetEditor draft={localSelected} readOnly={readOnly || !!recoveryError} registerBarrier={registerEditor} onUploadBusyChange={busy => localSession.setUploadBusy(localSelected.id, busy)} onChange={patch => editDraft(localSelected.id, patch)} onReferencesChange={ids => {
    const current = localSession.getSnapshot().drafts.find(item => item.id === localSelected.id);
    if (current) editDraft(current.id, { generation: { model_config_id: null, input_text: '', image_settings: null, video_settings: null, ...current.generation, reference_media_ids: ids } });
  }} onRetry={() => void createDraft(localSelected.id)} onRemove={() => void removeDraft(localSelected.id)}/>
    : detail && detail.id === selectedId ? <EpisodeAssetEditor key={detail.id} asset={detail} scope={scope} actor={actor} readOnly={readOnly} initialGeneration={privateInitial[detail.id]}
      registerBarrier={registerEditor} onArtwork={receiveArtwork} onRemove={() => void removeSaved(overrides[detail.id] ?? detail)} onShare={shareTo ? async () => { try { await assetLibraries.link(shareTo, detail.id); setNotice('素材已共享到项目库。'); } catch (cause) { setNotice(errorMessage(cause)); } } : undefined}/>
    : <section className="standard-object-panel standard-asset-empty" aria-label="素材工作区">{detailLoading ? <div className="standard-asset-workspace-state" role="status"><Spin/><strong>正在加载作品</strong></div> : detailError ? <div className="standard-asset-workspace-state" role="alert"><TriangleAlert size={30}/><strong>作品加载失败</strong><p>{detailError}</p><Button onClick={() => selectedId && void readDetail(selectedId)}>重新读取素材</Button></div> : <div className="standard-asset-workspace-state"><ImageIcon size={30}/><strong>选择一个对象开始制作</strong>{!readOnly && <Button onClick={() => void addDraft()}>新增素材</Button>}</div>}</section>;

  return <section className="standard-assets-list" aria-label={title}>
    <header className="standard-assets-heading"><h2><span className="standard-assets-title-text">本集素材</span> <small>{categoryCounts ? categoryCounts.all + drafts.length : '—'}</small></h2><div className="standard-assets-tools">{!readOnly && <Button type="text" aria-label="新增素材" title="新增素材" icon={<Plus size={17}/>} disabled={!!recoveryError} onClick={() => void addDraft()}/>}<span className="standard-extraction-tool" title="从剧本提取素材"><span className="standard-extraction-icon" aria-hidden="true"><Sparkles size={17}/></span>{toolbar}</span>{importFrom && !readOnly && <Button type="text" aria-label="导入素材" title="从项目素材库导入" icon={<Upload size={17}/>} onClick={() => void openImports()}/>}</div></header>
    <div className="standard-assets-filters"><Input prefix={<Search size={15} aria-hidden="true"/>} value={query} onChange={event => { setQuery(event.target.value); setOffset(0); }} aria-label="搜索素材" placeholder="搜索名称或描述" allowClear/><div className="standard-asset-category-tabs" aria-label="素材类别">{Object.entries(labels).map(([value, label]) => <button type="button" key={value} aria-label={label} aria-pressed={kind === value} onClick={() => { setKind(value as AssetKind | 'all'); setOffset(0); }}><span>{label}</span><small>{categoryCounts ? categoryCounts[value as AssetKind | 'all'] + drafts.filter(item => value === 'all' || item.kind === value).length : '—'}</small></button>)}</div></div>
    {countError && <Alert type="warning" message={countError} action={<Button size="small" onClick={() => setCountRevision(value => value + 1)}>重试读取分类数量</Button>}/>}
    {notice && <Alert type="warning" message={notice} closable onClose={() => setNotice('')}/>}
    {recoveryError && <Alert type="error" message={recoveryError} action={<><Button onClick={() => {
      let content = JSON.stringify(draftsRef.current, null, 2);
      try { content = localStorage.getItem(recoveryKey) ?? content; } catch { /* The current in-memory draft can still be exported. */ }
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
      const link = document.createElement('a'); link.href = url; link.download = `episode-${scope.episodeId}-asset-drafts.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }}>下载本机恢复记录</Button><Button onClick={async () => {
      if (!await confirmAction('明确清除本机素材恢复记录？此操作不会删除服务端素材。')) return;
      localSession.discardRecovery();
    }}>清除本机恢复记录</Button></>}/>}
    <div className="standard-assets-scroll" aria-busy={loading}>{error && (known.length ? <Alert type="error" showIcon message={error} action={<Button onClick={refresh}>重新加载素材</Button>}/> : <div className="standard-assets-list-state" role="alert"><TriangleAlert size={30}/><strong>列表加载失败</strong><p>{error}</p><Button onClick={refresh}>重新加载素材</Button></div>)}
      {loading && !known.length ? <div role="status" aria-label="正在加载素材列表">{[0, 1, 2].map(index => <div className="standard-asset-skeleton" key={index}><Skeleton.Image active/><Skeleton active title={false} paragraph={{ rows: 1 }}/></div>)}</div> : known.map(item => <StandardEpisodeAssetCard key={item.id} asset={item} selected={selectedId === item.id} disabled={false} entryRef={node => { if (node) entries.current.set(item.id, node); else entries.current.delete(item.id); }} onOpen={() => void selectAsset(item.id, item)}/>)}
      {visibleDrafts.map(item => <StandardEpisodeAssetCard key={item.id} asset={item} selected={selectedId === item.id} disabled={false} entryRef={node => { if (node) entries.current.set(item.id, node); else entries.current.delete(item.id); }} onOpen={() => void selectAsset(item.id)}/>)}
      {!loading && !error && !known.length && !visibleDrafts.length && <div className="standard-assets-list-state"><ImageIcon size={30}/><strong>{query ? '没有匹配的条目' : '还没有素材'}</strong><p>{query ? '试试其他关键词或分类。' : '从剧本提取，或手动新增。'}</p>{!readOnly && !query && <Button onClick={() => void addDraft()}>新增素材</Button>}</div>}
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
