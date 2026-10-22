import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { Alert, Button, Input, Popover, Select, Skeleton, Upload } from 'antd';
import { Check, History, MoreHorizontal, PanelRightClose, PanelRightOpen, Share2, Trash2, X } from 'lucide-react';
import { assetLibraries, type AssetDraft, type AssetRead, type AssetScope, type LibraryAssetRead } from '../../api/modules/assets';
import type { CreationDraftValue } from '../../api/modules/creation-drafts';
import { ApiError, errorMessage } from '../../api/http';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { confirmAction } from '../../components/ui/confirm';
import { ImagePicker } from '../media-library/ImagePicker';
import { useEpisodeCreationControls } from '../projects/EpisodeCreationWorkspace';
import { GenerationComposer } from '../projects/GenerationComposer';
import { useCreationDraft } from '../projects/useCreationDraft';
import { releaseStandardSession, retainStandardSession } from '../projects/standard-session-registry';
import type { NavigationBarrier } from '../projects/writing-navigation';
import { assetConfirmRequest } from '../projects/workflow-contract';
import { ConfigSelect } from '../generations/ConfigSelect';
import { AssetEditingSession, assetRecoveryKey, assetRecoveryStorage } from './asset-editing-session';
import { AssetGenerationParameters, emptyAssetGeneration, StandardAssetGeneration } from './StandardAssetGeneration';
import { PrivateAssetReferences, type AssetReferenceChange } from './PrivateAssetReferences';
import { AssetPromptInput } from './AssetPromptInput';
import type { AssetPromptReference } from './asset-prompt-references';
import type { LocalAssetDraft } from './episode-asset-drafts';
import { useImageGroup } from '../image-tools/useImageGroup';
import { ImageGroupPreview } from '../image-tools/ImageGroupPreview';
import { ImageGroupHistory } from '../image-tools/ImageGroupHistory';

const labels = { character: '角色', scene: '场景', prop: '道具' };

function AssetInformation({ value, disabled, onChange }: {
  value: Pick<LocalAssetDraft, 'name' | 'kind' | 'description'>; disabled: boolean;
  onChange: (patch: Partial<Pick<LocalAssetDraft, 'name' | 'kind' | 'description'>>) => void;
}) {
  return <div className="standard-asset-information"><div className="standard-asset-name-type">
    <label>素材名称<Input aria-label="素材名称" value={value.name} placeholder="输入名称" maxLength={255} disabled={disabled} onChange={event => onChange({ name: event.target.value })}/></label>
    <label>素材类型<Select classNames={{ popup: { root: 'standard-creation-popup' } }} aria-label="素材类型" value={value.kind ?? undefined} placeholder="选择类型" disabled={disabled} options={Object.entries(labels).map(([value, label]) => ({ value, label }))} onChange={kind => onChange({ kind })}/></label>
  </div><label className="standard-asset-description">素材描述<Input.TextArea aria-label="描述" value={value.description} maxLength={8000}  disabled={disabled} placeholder="外观、特征或环境" onChange={event => onChange({ description: event.target.value })}/></label></div>;
}

function useSettingsPanel() {
  const controls = useEpisodeCreationControls();
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 700px)').matches);
  const [open, setOpen] = useState(() => !window.matchMedia('(max-width: 700px)').matches);
  const pane = useRef<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const query = window.matchMedia('(max-width: 700px)');
    const changed = () => { setMobile(query.matches); if (query.matches) setOpen(false); }; query.addEventListener('change', changed);
    return () => query.removeEventListener('change', changed);
  }, []);
  useEffect(() => {
    if (!mobile || !open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); requestAnimationFrame(() => {
        const target = trigger.current?.isConnected ? trigger.current : pane.current?.closest('.standard-object-panel')?.querySelector<HTMLElement>('button[aria-label="展开生成设置"]');
        target?.focus();
      }); }
      if (event.key !== 'Tab' || !pane.current || document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')) return;
      const nodes = [...pane.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]')].filter(node => node.getClientRects().length);
      const first = nodes[0]; const last = nodes.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', handler);
    requestAnimationFrame(() => pane.current?.querySelector<HTMLElement>('input:not(:disabled),button:not(:disabled)')?.focus());
    return () => document.removeEventListener('keydown', handler);
  }, [mobile, open]);
  const reveal = () => { trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; controls.revealPanel?.(); setOpen(true); };
  return { open, mobile, pane, reveal, close: () => { setOpen(false); if (mobile) requestAnimationFrame(() => {
    const target = trigger.current?.isConnected ? trigger.current : pane.current?.closest('.standard-object-panel')?.querySelector<HTMLElement>('button[aria-label="展开生成设置"]');
    target?.focus();
  }); } };
}

function ObjectPanel({ title, settings, actions, status, preview, children }: {
  title: string; settings: ReturnType<typeof useSettingsPanel>; actions?: ReactNode; status?: ReactNode; preview: ReactNode; children: ReactNode;
}) {
  return <section className={`standard-object-panel episode-asset-detail${settings.open ? '' : ' is-settings-collapsed'}`} aria-label={`素材 ${title}`}>
    <header className="standard-object-heading"><h3 title={title}>{title}</h3><div className="standard-asset-object-actions">{status}{actions}<Button type="text" aria-label={settings.open ? '收起生成设置' : '展开生成设置'} title={settings.open ? '收起生成设置' : '展开生成设置'} aria-expanded={settings.open} icon={settings.open ? <PanelRightClose size={17}/> : <PanelRightOpen size={17}/>} onClick={settings.open ? settings.close : settings.reveal}/></div></header>
    <div className={`standard-object-media${settings.open ? '' : ' is-settings-collapsed'}`}>
      <section className="standard-object-preview" aria-label="素材图片预览">{preview}</section>
      {settings.open && settings.mobile && <button className="standard-asset-settings-backdrop" aria-label="关闭设置遮罩" onClick={settings.close}/>}
      <div ref={settings.pane} className={`standard-object-settings${settings.mobile && settings.open ? ' is-mobile-open' : ''}`} hidden={!settings.open} role={settings.mobile && settings.open ? 'dialog' : undefined} aria-modal={settings.mobile && settings.open ? true : undefined} aria-label="素材生成设置">
        <header className="standard-asset-settings-heading"><h4>生成设置</h4><Button type="text" aria-label="关闭生成设置" title="收起生成设置" icon={<X size={17}/>} onClick={settings.close}/></header>
        <div className="standard-object-settings-scroll standard-asset-settings-content">{children}</div>
      </div>
    </div>
  </section>;
}

export function LocalAssetEditor({ draft, readOnly, onChange, onRetry, onRemove, registerBarrier, onUploadBusyChange, onReferencesChange }: {
  draft: LocalAssetDraft; readOnly: boolean; onChange: (value: Partial<LocalAssetDraft>) => void; onRetry: () => void; onRemove: () => void;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
  onUploadBusyChange: (busy: boolean) => void;
  onReferencesChange: (ids: string[], references: AssetReferenceChange) => void;
}) {
  const settings = useSettingsPanel();
  const [modelFeedback, setModelFeedback] = useState<HTMLDivElement | null>(null);
  const [referenceImages, setReferenceImages] = useState<AssetPromptReference[]>([]);
  const value = draft.generation ?? emptyAssetGeneration;
  const ignoredReferenceState = useCallback(() => {}, []);
  const [uploadBusy, setUploadBusy] = useState(false);
  const uploading = useRef(false);
  const handleUploadBusy = useCallback((busy: boolean) => {
    uploading.current = busy; setUploadBusy(busy); onUploadBusyChange(busy);
  }, [onUploadBusyChange]);
  useEffect(() => { registerBarrier({ hasUnsettled: () => uploading.current, flush: async () => !uploading.current }); return () => registerBarrier(null); }, [registerBarrier]);
  useEffect(() => {
    if (settings.mobile) return;
    settings.reveal();
    const frame = requestAnimationFrame(() => settings.pane.current?.querySelector<HTMLInputElement>('input[aria-label="素材名称"]')?.focus());
    return () => cancelAnimationFrame(frame);
  }, [draft.id]);
  const editPrivate = (patch: Partial<CreationDraftValue>) => onChange({ generation: { ...value, ...patch } });
  return <ObjectPanel title={draft.name || '未命名素材'} settings={settings} preview={<div className="standard-asset-empty-preview" role="status">还没有生成图片</div>}>
    <AssetInformation value={draft} disabled={readOnly} onChange={onChange}/>
    {draft.message && <StudioFeedback type={draft.status === 'error' ? 'error' : 'warning'} title={`新素材 · ${draft.name || '未命名素材'}`} noticeKey={`asset-create:${draft.id}`} message={draft.message} actions={draft.status === 'unknown' || draft.status === 'error' ? <Button onClick={onRetry}>{draft.status === 'unknown' ? '核对原创建请求' : '重试创建'}</Button> : undefined}/>}
    <PrivateAssetReferences assetId={draft.id} projectId="" privateIds={value.reference_media_ids} saved={null} disabled={readOnly || draft.status === 'saving'} onChange={onReferencesChange} onState={ignoredReferenceState} onImagesChange={setReferenceImages} onBusyChange={handleUploadBusy}/>
    <GenerationComposer feedbackRef={setModelFeedback} prompt={value.input_text} onPrompt={input_text => editPrivate({ input_text })} promptInput={id => <AssetPromptInput id={id} value={value.input_text} onChange={input_text => editPrivate({ input_text })} references={referenceImages} disabled={readOnly}/>} disabled={readOnly} model={<ConfigSelect presentation="standard-creation" optionLayout="compact" feedbackContainer={modelFeedback} kind="image" value={value.model_config_id ?? undefined} onChange={id => editPrivate({ model_config_id: id ?? null })} onResolvedChange={id => { if (id && id !== value.model_config_id && !readOnly) editPrivate({ model_config_id: id }); }} disabled={readOnly} label="生图模型"/>} parameters={<AssetGenerationParameters countPlacement="options" value={value} disabled={readOnly} onChange={editPrivate}/>} sendDisabled busy={false} onSend={() => {}}/>
    {!readOnly && <Button type="text" size="small" danger className="standard-local-asset-discard" aria-label="放弃本机素材草稿" disabled={draft.status === 'saving' || uploadBusy} onClick={onRemove}>放弃本机素材草稿</Button>}
  </ObjectPanel>;
}

export function EpisodeAssetEditor({ asset, scope, actor, readOnly, initialGeneration, registerBarrier, onArtwork, onRemove }: {
  asset: LibraryAssetRead; scope: Extract<AssetScope, { kind: 'project' }>; actor: string; readOnly: boolean;
  initialGeneration?: CreationDraftValue; registerBarrier: (barrier: NavigationBarrier | null) => void;
  onArtwork: (asset: LibraryAssetRead) => void; onRemove: () => void;
}) {
  const settings = useSettingsPanel();
  const resourceKey = `asset:${asset.id}`;
  const session = useMemo(() => retainStandardSession(actor, resourceKey, () => new AssetEditingSession(asset, assetLibraries.update,
    assetRecoveryStorage(assetRecoveryKey(actor, scope.projectId, asset.id), `short-drama:asset-editor:v1:${actor}:${scope.projectId}:${asset.id}`), 1000, assetLibraries.detail),
  { label: `素材 · ${asset.name}`, projectId: scope.projectId }), [asset.id, actor, scope.projectId, resourceKey]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => () => releaseStandardSession(actor, resourceKey), [session, actor, resourceKey]);
  useEffect(() => { session.observe(asset); }, [session, asset]);
  const barriers = useRef<{ private: NavigationBarrier | null }>({ private: null });
  const registerPrivate = useCallback((barrier: NavigationBarrier | null) => { barriers.current.private = barrier; }, []);
  const draft = useCreationDraft({ projectId: scope.projectId, objectId: asset.id, purpose: 'asset_image',
    sourceVersion: state.saved.row_version, initialValue: initialGeneration ?? emptyAssetGeneration, readOnly, registerBarrier: registerPrivate });
  const initialGenerationKey = initialGeneration ? JSON.stringify(initialGeneration) : '';
  const currentGenerationKey = JSON.stringify(draft.value);
  useEffect(() => {
    if (!initialGeneration || !draft.loaded || readOnly || draft.saved) return;
    if (draft.status === 'saved' && currentGenerationKey !== initialGenerationKey) draft.edit(initialGeneration);
    if (draft.status === 'idle' || draft.status === 'local' || draft.status === 'unsaved') void draft.flush();
  }, [initialGeneration, initialGenerationKey, currentGenerationKey, draft.loaded, draft.status, draft.saved, draft.flush, draft.edit, readOnly]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toolBusy, setToolBusy] = useState(false);
  const [artworkError, setArtworkError] = useState('');
  const [generationBusy, setGenerationBusy] = useState(false);
  const [showImages, setShowImages] = useState(false);
  const [toolsOrigin, setToolsOrigin] = useState<'main' | 'history' | null>(null);
  const toolsTrigger = useRef<HTMLButtonElement | null>(null);
  const toolsMenus = useRef<Record<'main' | 'history', HTMLDivElement | null>>({ main: null, history: null });
  const closeHistory = () => { setToolsOrigin(null); setHistoryOpen(false); };

  const [reviewOpen, setReviewOpen] = useState(false);
  const [latest, setLatest] = useState<AssetRead | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState('');
  const reviewRequest = useRef<AbortController | null>(null);
  const operation = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; reviewRequest.current?.abort(); }; }, []);
  const busyRef = useRef(false); busyRef.current = busy || toolBusy || generationBusy;
  useEffect(() => {
    registerBarrier({ hasUnsettled: () => session.hasUnsettled() || !!barriers.current.private?.hasUnsettled() || busyRef.current,
      hasMemoryOnlyChanges: () => session.hasMemoryOnlyChanges() || !!barriers.current.private?.hasMemoryOnlyChanges?.(),
      flush: async () => {
        if (busyRef.current || !await session.flush()) return false;
        if (barriers.current.private?.hasUnsettled() && !await barriers.current.private.flush()) return false;
        return !session.hasUnsettled();
      },
    });
    return () => registerBarrier(null);
  }, [registerBarrier, session]);
  useEffect(() => { onArtwork(state.asset); }, [state.asset, onArtwork]);
  useEffect(() => {
    const key = `asset:${scope.projectId}:${asset.id}`;
    window.dispatchEvent(new CustomEvent('creation-save-state', { detail: { key, status: state.status === 'dirty' ? 'unsaved' : state.status, message: state.message } }));
    return () => { window.dispatchEvent(new CustomEvent('creation-save-state', { detail: { key, status: null } })); };
  }, [state.status, state.message, scope.projectId, asset.id]);
  const blocked = ['conflict', 'error'].includes(state.status);
  const artworkChanged = useCallback(() => {
    void assetLibraries.detail(asset.id).then(next => {
      if (!alive.current) return;
      const current = session.getSnapshot();
      if (next.row_version === current.saved.row_version) return;
      if (session.hasUnsettled()) session.conflict('素材主图已更新，本页输入已保留，请核对最新版本。');
      else session.accept(next);
    }).catch(cause => { if (alive.current) setArtworkError(errorMessage(cause)); });
  }, [asset.id, session]);
  const images = useImageGroup('asset', asset.id, artworkChanged);
  const candidatesChanged = useCallback(() => { void images.refresh(); }, [images.refresh]);
  const saveBeforeGenerate = useCallback(async () => {
    if (operation.current) return null;
    return session.flush();
  }, [session]);
  const versionConflict = useCallback((cause: unknown) => {
    if (cause instanceof ApiError && (cause.code === 'asset_version_conflict' || cause.status === 409 && cause.code === 'HTTP_409')) session.conflict();
  }, [session]);

  function downloadDraft() {
    let content = JSON.stringify({ asset_id: asset.id, scope, row_version: state.saved.row_version, draft: state.asset }, null, 2);
    if (state.recoveryBlocked) {
      try { content = localStorage.getItem(assetRecoveryKey(actor, scope.projectId, asset.id)) ?? localStorage.getItem(`short-drama:asset-editor:v1:${actor}:${scope.projectId}:${asset.id}`) ?? content; }
      catch { setArtworkError('浏览器无法读取原恢复记录，下载中仅包含当前可读取的素材。'); }
    }
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `asset-${asset.id}-draft.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function reviewConflict() {
    reviewRequest.current?.abort(); const controller = new AbortController(); reviewRequest.current = controller;
    setReviewOpen(true); setReviewLoading(true); setReviewError(''); setLatest(null);
    try { const remote = await assetLibraries.detail(asset.id, controller.signal); if (!controller.signal.aborted) setLatest(remote); }
    catch (cause) { if (!controller.signal.aborted) setReviewError(errorMessage(cause)); }
    finally { if (!controller.signal.aborted) setReviewLoading(false); }
  }
  async function replaceArtwork(mediaId: string) {
    const saved = await session.flush();
    if (!saved) return false;
    const next = await assetLibraries.confirm(asset.id, { ...assetConfirmRequest(saved, mediaId), acknowledge_stale_source: true });
    if (alive.current) { session.accept(next); await images.refresh(); }
    return true;
  }
  async function upload(file: File) {
    if (operation.current || readOnly || blocked || generationBusy || toolBusy) return false;
    if (file.size > 20 * 1024 * 1024) { setArtworkError('图片不能超过 20 MiB。'); return false; }
    operation.current = true; setBusy(true); setArtworkError('');
    try { const saved = await session.flush(); if (!saved) return false; const result = await assetLibraries.upload(asset.id, file); await replaceArtwork(result.media_id); }
    catch (cause) { if (alive.current) { versionConflict(cause); setArtworkError(errorMessage(cause)); } }
    finally { operation.current = false; if (alive.current) setBusy(false); }
    return false;
  }
  async function addMedia(mediaId: string) {
    if (operation.current || readOnly || blocked || generationBusy || toolBusy) return false;
    operation.current = true; setBusy(true); setArtworkError('');
    try { const saved = await session.flush(); if (!saved) return false; await assetLibraries.addCandidate(asset.id, mediaId); return await replaceArtwork(mediaId); }
    catch (cause) { if (alive.current) { versionConflict(cause); setArtworkError(errorMessage(cause)); } return false; }
    finally { operation.current = false; if (alive.current) setBusy(false); }
  }
  const beforeImageChange = useCallback(async () => !!await session.flush(), [session]);
  const addReference = useCallback(async (mediaId: string) => {
    if (!draft.loaded) throw new Error('个人生成设置仍在载入');
    const reference_media_ids = [...new Set([...draft.value.reference_media_ids, mediaId])];
    if (reference_media_ids.length > 16) throw new Error('私人参考图最多 16 张');
    draft.edit({ reference_media_ids });
    if (!await draft.flush()) throw new Error('私人参考图未能保存，请核对生成设置');
  }, [draft.loaded, draft.value.reference_media_ids, draft.edit, draft.flush]);
  const preview = <>
    <ImageGroupPreview presentation="asset-creation" group={images.group} loading={images.loading} error={images.error} label={state.asset.name || '素材'}
      emptyCount={draft.value.image_settings?.count}
      primaryStale={state.saved.state === 'unconfirmed'}
      disabled={readOnly || generationBusy || busy || blocked} imageModelId={draft.value.model_config_id ?? undefined}
      onRefresh={() => void images.refresh()} onBeforeChange={beforeImageChange} onChanged={artworkChanged} onAddReference={addReference} onBusyChange={setToolBusy}/>
    {state.saved.state === 'unconfirmed' && state.saved.media_id && <StudioFeedback type="warning" quiet title={`素材图片 · ${state.asset.name}`} noticeKey={`asset-stale:${asset.id}`} message="素材设定已变化，请重新生成，或将核对后的图片设为主图。"/>}
    {artworkError && <StudioFeedback type="error" title={`素材图片 · ${state.asset.name}`} noticeKey={`asset-artwork:${asset.id}`} message={artworkError} actions={<Button onClick={() => { artworkChanged(); void images.refresh(); }}>重新读取图片</Button>}/>}
  </>;
  const status = <span className={`standard-asset-status${state.saved.media_id ? ' is-success' : ''}`}>{state.saved.media_id && <Check size={12}/>} {state.saved.media_id ? '已生图' : '尚未制作'}</span>;
  const candidateHistory = <ImageGroupHistory kind="asset" targetId={asset.id} disabled={readOnly || busy || generationBusy || blocked} onBeforeChange={beforeImageChange} onChanged={() => { artworkChanged(); void images.refresh(); }}/>;
  const renderActions = (origin: 'main' | 'history') => !readOnly && <Popover trigger="click" placement={origin === 'history' ? 'topLeft' : 'bottomRight'} overlayClassName="standard-creation-popup" open={toolsOrigin === origin} onOpenChange={open => setToolsOrigin(open ? origin : null)} afterOpenChange={open => { if (open) toolsMenus.current[origin]?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus(); }} content={<div ref={element => { toolsMenus.current[origin] = element; }} className="standard-asset-more-tools" onKeyDown={event => {
    if (event.key !== 'Escape') return;
    event.preventDefault(); event.stopPropagation(); setToolsOrigin(null);
    requestAnimationFrame(() => toolsTrigger.current?.focus());
  }}>
    <Upload accept="image/png,image/jpeg,image/webp" showUploadList={false} disabled={busy || generationBusy || blocked} beforeUpload={file => { closeHistory(); return upload(file); }}><Button type="text" disabled={busy || generationBusy || blocked}>上传作品图片</Button></Upload>
    <Button type="text" disabled={busy || generationBusy || blocked} onClick={() => { closeHistory(); setShowImages(true); }}>从资产库选择</Button>
    <div className="standard-action-divider" role="separator"/>
    <Button type="text" danger aria-label="删除素材" icon={<Trash2 size={15}/>} disabled={busy || generationBusy || blocked} onClick={() => { closeHistory(); onRemove(); }}>删除素材</Button>
  </div>}><Button type="text" aria-label="素材更多操作" title="更多操作" aria-haspopup="true" aria-expanded={toolsOrigin === origin} icon={origin === 'main' ? <MoreHorizontal size={17}/> : undefined} onClick={event => { if (event.currentTarget instanceof HTMLButtonElement) toolsTrigger.current = event.currentTarget; }} onKeyDown={event => { if (event.key === 'Escape' && toolsOrigin === origin) { event.preventDefault(); event.stopPropagation(); setToolsOrigin(null); } }}>{origin === 'history' ? '素材操作' : null}</Button></Popover>;
  const historyActions = <div className="standard-asset-history-actions">{renderActions('history')}</div>;
  return <>
    <ObjectPanel title={state.asset.name || '未命名素材'} settings={settings} status={status} preview={preview} actions={<><Button type="text" aria-label="生成记录" title="生成记录" icon={<History size={16}/>} onClick={() => { setToolsOrigin(null); setHistoryOpen(true); }}/>{renderActions('main')}</>}>
      <AssetInformation value={state.asset} disabled={readOnly || busy || toolBusy || generationBusy || state.recoveryBlocked} onChange={patch => session.edit(patch as Partial<AssetDraft>)}/>
      {state.status === 'error' && <StudioFeedback type="error" title={`素材保存 · ${state.asset.name}`} noticeKey={`asset-save:${asset.id}`} message={state.message} actions={state.recoveryBlocked ? <><Button onClick={downloadDraft}>下载本机恢复记录</Button><Button onClick={async () => { if (await confirmAction('明确清除本机素材恢复记录？请先下载原记录备份。服务端素材会保留。', { className: 'standard-creation-dialog' })) session.discardRecovery(); }}>清除本机恢复记录</Button></> : <Button disabled={readOnly} onClick={() => void session.retry()}>重试保存</Button>}/>}
      {state.status === 'conflict' && <StudioFeedback type="warning" title={`素材保存 · ${state.asset.name}`} noticeKey={`asset-save:${asset.id}`} message={state.message} actions={<><Button onClick={() => void reviewConflict()}>核对最新版本</Button><Button onClick={downloadDraft}>下载素材草稿</Button></>}/>}
      <StandardAssetGeneration asset={state.asset} scope={scope} draft={draft} readOnly={readOnly} disabled={blocked || busy || toolBusy} historyOpen={historyOpen} onCloseHistory={closeHistory} onSaveBeforeGenerate={saveBeforeGenerate} onCandidatesChanged={candidatesChanged} onSubmissionBusyChange={setGenerationBusy} onVersionConflict={versionConflict} candidateHistory={candidateHistory} historyActions={historyActions}/>
    </ObjectPanel>
    {showImages && <ImagePicker purpose="artwork" presentation="standard-creation" busy={busy} projectId={scope.projectId} onClose={() => setShowImages(false)} onSelect={addMedia}/>}
    {reviewOpen && <Dialog title="核对素材版本" className="asset-import-dialog standard-creation-dialog" onClose={() => { reviewRequest.current?.abort(); setReviewOpen(false); }}>
      <div className="asset-import-body">{reviewLoading ? <Skeleton active paragraph={{ rows: 3 }}/> : reviewError ? <Alert type="error" message={reviewError} action={<Button onClick={() => void reviewConflict()}>重试读取最新版本</Button>}/> : latest && <><h4>本页草稿</h4><p>{state.asset.name} · {labels[state.asset.kind]}</p><p>{state.asset.description}</p><h4>服务端最新版本</h4><p>{latest.name} · {labels[latest.kind]}</p><p>{latest.description}</p>{latest.image?.url && <PreviewImage presentation="standard-creation" src={latest.image.url} alt="服务端当前图片"/>}</>}</div>
      <div className="production-dialog-footer"><Button onClick={downloadDraft}>下载素材草稿</Button><Button danger disabled={!latest || reviewLoading} onClick={() => { if (latest) { session.reconcile(latest, false); setReviewOpen(false); } }}>放弃草稿并载入最新版本</Button><Button type="primary" disabled={!latest || reviewLoading} onClick={() => { if (latest) { session.reconcile(latest, true); setReviewOpen(false); } }}>已核对，保留草稿继续编辑</Button></div>
    </Dialog>}
  </>;
}
