import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { Alert, Button, Input, Select, Skeleton, Upload } from 'antd';
import { History, PanelRightClose, PanelRightOpen, Share2, Trash2, X } from 'lucide-react';
import { assetLibraries, type AssetDraft, type AssetCandidate, type AssetRead, type AssetScope, type LibraryAssetRead } from '../../api/modules/assets';
import type { CreationDraftValue } from '../../api/modules/creation-drafts';
import { ApiError, errorMessage } from '../../api/http';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { Dialog } from '../../components/ui/Dialog';
import { confirmAction } from '../../components/ui/confirm';
import { ImagePicker } from '../media-library/ImagePicker';
import { useEpisodeCreationControls } from '../projects/EpisodeCreationWorkspace';
import { GenerationComposer } from '../projects/GenerationComposer';
import { useCreationDraft } from '../projects/useCreationDraft';
import type { NavigationBarrier } from '../projects/writing-navigation';
import { assetConfirmRequest } from '../projects/workflow-contract';
import { ConfigSelect } from '../generations/ConfigSelect';
import { AssetEditingSession, assetRecoveryKey, assetRecoveryStorage } from './asset-editing-session';
import { AssetGenerationParameters, emptyAssetGeneration, StandardAssetGeneration } from './StandardAssetGeneration';
import { PrivateAssetReferences } from './PrivateAssetReferences';
import type { LocalAssetDraft } from './episode-asset-drafts';

const labels = { character: '角色', scene: '场景', prop: '道具' };

function AssetInformation({ value, disabled, onChange }: {
  value: Pick<LocalAssetDraft, 'name' | 'kind' | 'description'>; disabled: boolean;
  onChange: (patch: Partial<Pick<LocalAssetDraft, 'name' | 'kind' | 'description'>>) => void;
}) {
  return <div className="standard-asset-information"><div className="standard-asset-name-type">
    <label>素材名称<Input aria-label="素材名称" value={value.name} placeholder="输入名称" maxLength={255} disabled={disabled} onChange={event => onChange({ name: event.target.value })}/></label>
    <label>类型<Select aria-label="素材类型" value={value.kind ?? undefined} placeholder="选择类型" disabled={disabled} options={Object.entries(labels).map(([value, label]) => ({ value, label }))} onChange={kind => onChange({ kind })}/></label>
  </div><label className="standard-asset-description">描述<Input.TextArea aria-label="描述" value={value.description} maxLength={8000} autoSize={{ minRows: 3, maxRows: 6 }} disabled={disabled} placeholder="外观、特征或环境" onChange={event => onChange({ description: event.target.value })}/></label></div>;
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

function ObjectPanel({ title, settings, actions, preview, children }: {
  title: string; settings: ReturnType<typeof useSettingsPanel>; actions?: ReactNode; preview: ReactNode; children: ReactNode;
}) {
  return <section className={`standard-object-panel episode-asset-detail${settings.open ? '' : ' is-settings-collapsed'}`} aria-label={`素材 ${title}`}>
    <header className="standard-object-heading"><h3 title={title}>{title}</h3><div className="standard-asset-object-actions">{actions}{!settings.open && <Button type="text" aria-label="展开生成设置" title="展开生成设置" icon={<PanelRightOpen size={17}/>} onClick={settings.reveal}/>}</div></header>
    <section className="standard-object-preview" aria-label="素材图片预览">{preview}</section>
    {settings.open && settings.mobile && <button className="standard-asset-settings-backdrop" aria-label="关闭生成设置" onClick={settings.close}/>}
    <div ref={settings.pane} className={`standard-object-settings${settings.mobile && settings.open ? ' is-mobile-open' : ''}`} hidden={!settings.open} role={settings.mobile && settings.open ? 'dialog' : undefined} aria-modal={settings.mobile && settings.open ? true : undefined} aria-label="素材生成设置">
      <header className="standard-asset-settings-heading"><h4>生成设置</h4><Button type="text" aria-label="收起生成设置" title="收起生成设置" icon={settings.mobile ? <X size={17}/> : <PanelRightClose size={17}/>} onClick={settings.close}/></header>
      <div className="standard-asset-settings-content">{children}</div>
    </div>
  </section>;
}

export function LocalAssetEditor({ draft, readOnly, onChange, onRetry, onRemove, registerBarrier, onUploadBusyChange }: {
  draft: LocalAssetDraft; readOnly: boolean; onChange: (value: Partial<LocalAssetDraft>) => void; onRetry: () => void; onRemove: () => void;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
  onUploadBusyChange: (busy: boolean) => void;
}) {
  const settings = useSettingsPanel();
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
  return <ObjectPanel title={draft.name || '未命名素材'} settings={settings} actions={!readOnly && <Button type="text" danger aria-label="放弃本机素材草稿" disabled={draft.status === 'saving' || uploadBusy} icon={<Trash2 size={15}/>} onClick={onRemove}/>} preview={<div className="standard-asset-preview-empty"><span>待制作</span><p>填写名称和类型后保存素材，再生成图片。</p></div>}>
    <AssetInformation value={draft} disabled={readOnly} onChange={onChange}/>
    <div className="standard-asset-save-feedback" role="status">{draft.status === 'saving' ? '正在保存到服务端…' : '本机草稿已保留'}</div>
    {draft.message && <Alert type={draft.status === 'error' ? 'error' : 'warning'} message={draft.message} action={draft.status === 'unknown' || draft.status === 'error' ? <Button loading={false} onClick={onRetry}>{draft.status === 'unknown' ? '核对原创建请求' : '重试创建'}</Button> : undefined}/>}
    <PrivateAssetReferences assetId={draft.id} projectId="" episodeId="" privateIds={value.reference_media_ids} saved={null} disabled={readOnly || draft.status === 'saving'} onChange={reference_media_ids => editPrivate({ reference_media_ids })} onState={ignoredReferenceState} onBusyChange={handleUploadBusy}/>
    <GenerationComposer prompt={value.input_text} onPrompt={input_text => editPrivate({ input_text })} disabled={readOnly} model={<ConfigSelect kind="image" value={value.model_config_id ?? undefined} onChange={id => editPrivate({ model_config_id: id ?? null })} onResolvedChange={id => { if (id && id !== value.model_config_id && !readOnly) editPrivate({ model_config_id: id }); }} disabled={readOnly} label="生图模型"/>} parameters={<AssetGenerationParameters value={value} disabled={readOnly} onChange={editPrivate}/>} sendDisabled busy={false} onSend={() => {}}/>
  </ObjectPanel>;
}

export function EpisodeAssetEditor({ asset, scope, actor, readOnly, initialGeneration, registerBarrier, onArtwork, onRemove, onShare }: {
  asset: LibraryAssetRead; scope: Extract<AssetScope, { kind: 'episode' }>; actor: string; readOnly: boolean;
  initialGeneration?: CreationDraftValue; registerBarrier: (barrier: NavigationBarrier | null) => void;
  onArtwork: (asset: LibraryAssetRead) => void; onRemove: () => void; onShare?: () => Promise<void>;
}) {
  const settings = useSettingsPanel();
  const session = useMemo(() => new AssetEditingSession(asset, assetLibraries.update,
    assetRecoveryStorage(assetRecoveryKey(actor, scope.projectId, scope.episodeId, asset.id))), [asset.id, actor, scope.projectId, scope.episodeId]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const disposal = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => { clearTimeout(disposal.current); return () => { disposal.current = setTimeout(() => session.dispose(), 0); }; }, [session]);
  const barriers = useRef<{ private: NavigationBarrier | null }>({ private: null });
  const registerPrivate = useCallback((barrier: NavigationBarrier | null) => { barriers.current.private = barrier; }, []);
  const draft = useCreationDraft({ projectId: scope.projectId, episodeId: scope.episodeId, objectId: asset.id, purpose: 'asset_image',
    sourceVersion: state.saved.row_version, initialValue: initialGeneration ?? emptyAssetGeneration, readOnly, registerBarrier: registerPrivate });
  const initialGenerationKey = initialGeneration ? JSON.stringify(initialGeneration) : '';
  const currentGenerationKey = JSON.stringify(draft.value);
  useEffect(() => {
    if (!initialGeneration || !draft.loaded || readOnly || draft.saved) return;
    if (draft.status === 'saved' && currentGenerationKey !== initialGenerationKey) draft.edit(initialGeneration);
    if (draft.status === 'local' || draft.status === 'unsaved') void draft.flush();
  }, [initialGeneration, initialGenerationKey, currentGenerationKey, draft.loaded, draft.status, draft.saved, draft.flush, draft.edit, readOnly]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [candidates, setCandidates] = useState<AssetCandidate[]>([]);
  const [candidateOffset, setCandidateOffset] = useState(0);
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [candidateLoading, setCandidateLoading] = useState(true);
  const [candidateLoaded, setCandidateLoaded] = useState(false);
  const [candidateError, setCandidateError] = useState('');
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [generationBusy, setGenerationBusy] = useState(false);
  const [showImages, setShowImages] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [latest, setLatest] = useState<AssetRead | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState('');
  const candidateRequest = useRef<AbortController | null>(null);
  const reviewRequest = useRef<AbortController | null>(null);
  const operation = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; candidateRequest.current?.abort(); reviewRequest.current?.abort(); }; }, []);
  const busyRef = useRef(false); busyRef.current = busy || generationBusy;
  useEffect(() => {
    registerBarrier({ hasUnsettled: () => session.hasUnsettled() || !!barriers.current.private?.hasUnsettled() || busyRef.current,
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
    const key = `asset:${scope.projectId}:${scope.episodeId}:${asset.id}`;
    window.dispatchEvent(new CustomEvent('creation-save-state', { detail: { key, status: state.status === 'dirty' ? 'unsaved' : state.status === 'shared' ? 'error' : state.status, message: state.message } }));
    return () => { window.dispatchEvent(new CustomEvent('creation-save-state', { detail: { key, status: null } })); };
  }, [state.status, state.message, scope.projectId, scope.episodeId, asset.id]);
  const blocked = ['conflict', 'error', 'shared'].includes(state.status);
  const selectedCandidate = candidates.find(item => item.id === previewId) ?? null;

  const loadCandidates = useCallback(async (append = false, showLatest = false) => {
    candidateRequest.current?.abort(); const controller = new AbortController(); candidateRequest.current = controller;
    setCandidateLoading(true);
    try {
      const page = await assetLibraries.candidates(asset.id, controller.signal, append ? candidateOffset : 0, 20);
      if (controller.signal.aborted) return;
      setCandidates(current => append ? [...current, ...page.items.filter(item => !current.some(prior => prior.id === item.id))] : page.items);
      setCandidateOffset((append ? candidateOffset : 0) + page.items.length); setCandidateTotal(page.total); setCandidateLoaded(true); setCandidateError('');
      if (showLatest && page.items[0] && page.items[0].media_id !== session.getSnapshot().saved.media_id) setPreviewId(page.items[0].id);
    } catch (cause) { if (!controller.signal.aborted) setCandidateError(`读取图片候选失败：${errorMessage(cause)}`); }
    finally { if (!controller.signal.aborted) setCandidateLoading(false); }
  }, [asset.id, candidateOffset, session]);
  useEffect(() => { void loadCandidates(); return () => candidateRequest.current?.abort(); }, [asset.id]);
  const candidatesChanged = useCallback(() => { void loadCandidates(false, true); }, [loadCandidates]);
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
      try { content = localStorage.getItem(assetRecoveryKey(actor, scope.projectId, scope.episodeId, asset.id)) ?? content; }
      catch { setCandidateError('浏览器无法读取原恢复记录，下载中仅包含当前可读取的素材。'); }
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
  async function confirmImage(candidate: Pick<AssetCandidate, 'media_id'>, ensureCandidate = false) {
    if (readOnly || busy || generationBusy || operation.current || blocked) return;
    if (!await confirmAction('确认采用此图？采用后会更新素材当前作品，候选仍保留。')) return;
    operation.current = true; setBusy(true); setCandidateError('');
    let shared = false; let acknowledge = false;
    try {
      const saved = await session.flush();
      if (!saved) return;
      if (ensureCandidate) await assetLibraries.addCandidate(asset.id, candidate.media_id);
      while (true) {
        try {
          const next = await assetLibraries.confirm(asset.id, { ...assetConfirmRequest(saved, candidate.media_id, shared), ...(acknowledge ? { acknowledge_stale_source: true } : {}) });
          session.accept(next); setPreviewId(null); await loadCandidates(); break;
        } catch (cause) {
          if (cause instanceof ApiError && cause.code === 'shared_asset_confirmation_required' && !shared && await confirmAction('采用图片会同步影响此素材的所有项目和分集引用。确定采用？')) { shared = true; continue; }
          if (cause instanceof ApiError && (cause.code === 'stale_source' || cause.code === 'stale_generation_source') && !acknowledge && await confirmAction('这张图片基于素材旧内容生成。核对后仍明确采用？')) { acknowledge = true; continue; }
          throw cause;
        }
      }
    } catch (cause) { versionConflict(cause); setCandidateError(errorMessage(cause)); }
    finally { operation.current = false; if (alive.current) setBusy(false); }
  }
  async function upload(file: File) {
    if (operation.current || readOnly || blocked || generationBusy) return false;
    if (file.size > 20 * 1024 * 1024) { setCandidateError('图片不能超过 20 MiB。'); return false; }
    operation.current = true; setBusy(true); setCandidateError('');
    try { const candidate = await assetLibraries.upload(asset.id, file); if (alive.current) { setCandidates(current => [candidate, ...current.filter(item => item.id !== candidate.id)]); setPreviewId(candidate.id); } }
    catch (cause) { if (alive.current) setCandidateError(errorMessage(cause)); }
    finally { operation.current = false; if (alive.current) setBusy(false); }
    return false;
  }
  async function addMedia(mediaId: string) {
    if (operation.current || readOnly || blocked || generationBusy) return false;
    operation.current = true; setBusy(true);
    try { const candidate = await assetLibraries.addCandidate(asset.id, mediaId); if (alive.current) { setCandidates(current => [candidate, ...current.filter(item => item.id !== candidate.id)]); setPreviewId(candidate.id); } return true; }
    catch (cause) { if (alive.current) setCandidateError(errorMessage(cause)); return false; }
    finally { operation.current = false; if (alive.current) setBusy(false); }
  }
  const shown = selectedCandidate ?? state.saved.image;
  const preview = <>
    <div className="standard-asset-work-tabs"><Button type={selectedCandidate ? 'text' : 'primary'} onClick={() => setPreviewId(null)}>当前作品</Button>{candidates.some(item => item.media_id !== state.saved.media_id) && <Button type={selectedCandidate ? 'primary' : 'text'} onClick={() => setPreviewId(candidates.find(item => item.media_id !== state.saved.media_id)?.id ?? null)}>新结果</Button>}</div>
    <div className="standard-asset-image-stage">{shown?.url ? <PreviewImage triggerClassName="standard-asset-preview-image" src={shown.url} alt={selectedCandidate ? `${state.asset.name}未采用结果` : state.asset.name}/> : <div className="standard-asset-preview-empty"><span>待制作</span><p>生成或上传图片后，在这里核对采用。</p></div>}</div>
    {selectedCandidate ? <div className="standard-asset-adoption"><span>{selectedCandidate.generation?.is_stale ? '未采用 · 来源已变化，需核对' : '未采用'}</span><Button type="primary" loading={busy} disabled={readOnly || generationBusy || blocked} onClick={() => void confirmImage(selectedCandidate)}>采用此结果</Button></div> : state.saved.state === 'unconfirmed' && state.saved.media_id ? <div className="standard-asset-adoption"><span>素材信息已修改，当前图片待确认</span>{!readOnly && <Button loading={busy} disabled={generationBusy || blocked} onClick={() => void confirmImage({ media_id: state.saved.media_id! }, true)}>重新确认当前图片</Button>}</div> : null}
    {candidateError && <Alert type="error" message={candidateError} action={<Button onClick={() => void loadCandidates()}>重新读取候选</Button>}/>}
  </>;
  const candidateHistory = <section className="standard-asset-candidate-history"><h4>图片候选</h4>{candidateLoading && !candidateLoaded ? <Skeleton active paragraph={{ rows: 2 }}/> : !candidates.length && !candidateError ? <p className="episode-help">暂无图片候选。</p> : <div className="image-candidate-grid">{candidates.map(candidate => <article className="image-candidate" key={candidate.id}><PreviewImage src={candidate.url} alt={`${state.asset.name}候选`}/><Button onClick={() => { setPreviewId(candidate.id); setHistoryOpen(false); }}>预览此结果</Button><Button disabled={readOnly || busy || generationBusy || blocked} onClick={() => void confirmImage(candidate)}>确认采用</Button></article>)}</div>}{candidateOffset < candidateTotal && <Button loading={candidateLoading} onClick={() => void loadCandidates(true)}>加载更多候选</Button>}</section>;
  return <>
    <ObjectPanel title={state.asset.name || '未命名素材'} settings={settings} preview={preview} actions={<><Button type="text" aria-label="生成记录" title="生成记录" icon={<History size={16}/>} onClick={() => setHistoryOpen(true)}/>{!readOnly && onShare && <Button type="text" aria-label="共享素材到项目" title="共享到项目" icon={<Share2 size={15}/>} disabled={busy || generationBusy || blocked} onClick={() => void onShare()}/>} {!readOnly && <Button type="text" danger aria-label="从本集移除素材" title="从本集移除素材" icon={<Trash2 size={15}/>} disabled={busy || generationBusy || blocked} onClick={onRemove}/>}</>}>
      <AssetInformation value={state.asset} disabled={readOnly || busy || generationBusy || state.recoveryBlocked} onChange={patch => session.edit(patch as Partial<AssetDraft>)}/>
      {state.status === 'shared' && <Alert type="warning" message={state.message} action={<Button disabled={readOnly} onClick={() => void session.approveShared()}>确认本次共享修改</Button>}/>}
      {state.status === 'error' && <Alert type="error" message={state.message} action={state.recoveryBlocked ? <><Button onClick={downloadDraft}>下载本机恢复记录</Button><Button onClick={async () => { if (await confirmAction('明确清除本机素材恢复记录？请先下载原记录备份。服务端素材会保留。')) session.discardRecovery(); }}>清除本机恢复记录</Button></> : <Button disabled={readOnly} onClick={() => void session.retry()}>重试保存</Button>}/>}
      {state.status === 'conflict' && <Alert type="warning" message={state.message} action={<><Button onClick={() => void reviewConflict()}>核对最新版本</Button><Button onClick={downloadDraft}>下载素材草稿</Button></>}/>}
      <StandardAssetGeneration asset={state.asset} scope={scope} draft={draft} readOnly={readOnly} disabled={blocked || busy} historyOpen={historyOpen} onCloseHistory={() => setHistoryOpen(false)} onSaveBeforeGenerate={saveBeforeGenerate} onCandidatesChanged={candidatesChanged} onSubmissionBusyChange={setGenerationBusy} onVersionConflict={versionConflict} candidateHistory={candidateHistory}/>
      {!readOnly && <div className="standard-asset-image-tools"><Upload accept="image/png,image/jpeg,image/webp" showUploadList={false} disabled={busy || generationBusy || blocked} beforeUpload={upload}><Button size="small" disabled={busy || generationBusy || blocked}>上传作品图片</Button></Upload><Button size="small" disabled={busy || generationBusy || blocked} onClick={() => setShowImages(true)}>从资产库选择</Button></div>}
    </ObjectPanel>
    {showImages && <ImagePicker busy={busy} projectId={scope.projectId} onClose={() => setShowImages(false)} onSelect={addMedia}/>}
    {reviewOpen && <Dialog title="核对素材版本" className="asset-import-dialog" onClose={() => { reviewRequest.current?.abort(); setReviewOpen(false); }}>
      <div className="asset-import-body">{reviewLoading ? <Skeleton active paragraph={{ rows: 3 }}/> : reviewError ? <Alert type="error" message={reviewError} action={<Button onClick={() => void reviewConflict()}>重试读取最新版本</Button>}/> : latest && <><h4>本页草稿</h4><p>{state.asset.name} · {labels[state.asset.kind]}</p><p>{state.asset.description}</p><h4>服务端最新版本</h4><p>{latest.name} · {labels[latest.kind]}</p><p>{latest.description}</p>{latest.image?.url && <PreviewImage src={latest.image.url} alt="服务端当前图片"/>}</>}</div>
      <div className="production-dialog-footer"><Button onClick={downloadDraft}>下载素材草稿</Button><Button danger disabled={!latest || reviewLoading} onClick={() => { if (latest) { session.reconcile(latest, false); setReviewOpen(false); } }}>放弃草稿并载入最新版本</Button><Button type="primary" disabled={!latest || reviewLoading} onClick={() => { if (latest) { session.reconcile(latest, true); setReviewOpen(false); } }}>已核对，保留草稿继续编辑</Button></div>
    </Dialog>}
  </>;
}
