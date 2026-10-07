import { confirmAction } from '../../components/ui/confirm';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ComponentProps, type FormEvent, type ReactNode } from 'react';
import { Alert, Button, Checkbox, Input, Pagination, Segmented, Skeleton, Upload } from 'antd';
import { ApiError, errorMessage } from '../../api/http';
import { http } from '../../api/http';
import { useAuth } from '../auth/AuthSession';
import { assetLibraries, type AssetCandidate, type AssetDraft, type AssetKind, type AssetRead, type AssetScope, type LibraryAssetRead } from '../../api/modules/assets';
import { Dialog } from '../../components/ui/Dialog';
import { ImagePicker } from '../media-library/ImagePicker';
import { Icon } from '../../components/ui/Icon';
import { useAssetLibrary } from './useAssetLibrary';
import { assetConfirmRequest, assetImagePresentation, assetPatch } from '../projects/workflow-contract';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { ReferenceImages } from '../generations/ReferenceImages';
import { AssetImageGeneration } from './AssetImageGeneration';
import { BatchLauncher, useBatchSelection } from '../generations/BatchGeneration';
import { CharacterVoicePanel } from '../projects/NativeVoicePanel';
import { EpisodeEditorSlot, useEpisodeCreation, useEpisodeCreationControls } from '../projects/EpisodeCreationWorkspace';
import { EpisodeAssetCard } from './EpisodeAssetCard';
import '../projects/workflow-refinement.css';
import type { NavigationBarrier } from '../projects/writing-navigation';

const labels: Record<AssetKind, string> = { character: '角色', scene: '场景', prop: '道具' };
const blank = (kind: AssetKind): AssetDraft => ({ kind, name: '', label: '', description: '', prompt: '', tags: [], scene_time: '' });
const scopeKey = (scope: AssetScope) => scope.kind === 'global' ? 'global' : scope.kind === 'project' ? `project:${scope.projectId}` : `episode:${scope.projectId}:${scope.episodeId}`;
const fields = (asset: AssetDraft | LibraryAssetRead) => ({ name: asset.name, label: asset.label, description: asset.description, prompt: asset.prompt, tags: asset.tags, scene_time: asset.scene_time });

async function loadAllLibrary(scope: AssetScope, kind: AssetKind, signal?: AbortSignal) {
  const items: LibraryAssetRead[] = []; let offset = 0; let total = 1;
  while (offset < total) { const page = await assetLibraries.list(scope, { kind, offset, limit: 100 }, signal); items.push(...page.items); total = page.total; offset += page.items.length; if (!page.items.length) break; }
  return items;
}

export function AssetLibraryPanel({
  scope, title, readOnly = false, importFrom, shareTo, legacyDownload, initialKind = 'character', onKindChange, toolbar, refreshToken = 0, registerBarrier,
}: {
  scope: AssetScope;
  title: string;
  readOnly?: boolean;
  importFrom?: AssetScope;
  shareTo?: AssetScope;
  legacyDownload?: () => void;
  initialKind?: AssetKind;
  onKindChange?: (kind: AssetKind) => void;
  toolbar?: ReactNode;
  refreshToken?: number;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  const auth = useAuth();
  const inEpisode = useEpisodeCreation();
  const creation = useEpisodeCreationControls();
  const dismissedSubject = useRef<string | null>(null);
  const DetailFrame = inEpisode ? EpisodeAssetDetail : Dialog;
  const copiesOnImport = auth.enabled && scope.kind === 'project' && importFrom?.kind === 'global';
  const importLibrary = importFrom?.kind === 'project' ? '项目' : auth.enabled ? '个人' : '全局';
  const [kind, setKind] = useState<AssetKind>(initialKind);
  const [query, setQuery] = useState('');
  const batchSelection = useBatchSelection(`${scopeKey(scope)}:${kind}:${query}`);
  const [offset, setOffset] = useState(0);
  const { items, total, loading, error, refresh } = useAssetLibrary(scope, kind, query, offset);
  useEffect(() => {
    if (scope.kind !== 'episode' || creation.subject?.type !== 'asset' || creation.subject.id === dismissedSubject.current) return;
    const current = items.find(item => item.id === creation.subject?.id);
    if (!current) return;
    const label = `${labels[current.kind]} · ${current.name}`;
    if (creation.subject.label !== label || creation.subject.revision !== current.row_version) creation.selectSubject?.({ type: 'asset', id: current.id, label, revision: current.row_version });
  }, [scope.kind, items, creation.subject, creation.selectSubject]);
  useEffect(() => { if (refreshToken) refresh(); }, [refreshToken, refresh]);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<AssetDraft>(() => blank(kind));
  const [selected, setSelected] = useState<LibraryAssetRead | null>(null);
  const [selectedSaved, setSelectedSaved] = useState<LibraryAssetRead | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [editorConflict, setEditorConflict] = useState(false);
  const [conflictReviewOpen, setConflictReviewOpen] = useState(false);
  const [conflictLatest, setConflictLatest] = useState<AssetRead | null>(null);
  const [conflictLoading, setConflictLoading] = useState(false);
  const [conflictError, setConflictError] = useState('');
  const [candidates, setCandidates] = useState<AssetCandidate[]>([]);
  const [candidateError, setCandidateError] = useState('');
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [candidateOffset, setCandidateOffset] = useState(0);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [candidateLoaded, setCandidateLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [generationSubmitting, setGenerationSubmitting] = useState(false);
  const [notice, setNotice] = useState('');
  const [importNoticeType, setImportNoticeType] = useState<'info' | 'error'>('info');
  const [imports, setImports] = useState<LibraryAssetRead[]>([]);
  const [showImport, setShowImport] = useState(false);
  const [importQuery, setImportQuery] = useState('');
  const [importLoading, setImportLoading] = useState(false);
  const importRequestRef = useRef<AbortController | null>(null);
  useEffect(() => () => importRequestRef.current?.abort(), []);
  const [showImages, setShowImages] = useState(false);
  const [closeReviewOpen, setCloseReviewOpen] = useState(false);
  const operationRef = useRef(false);
  const adoptionRef = useRef(false);
  const candidateRequestRef = useRef<AbortController | null>(null);
  const detailRequestRef = useRef<AbortController | null>(null);
  const conflictRequestRef = useRef<AbortController | null>(null);
  useEffect(() => () => {
    detailRequestRef.current?.abort(); conflictRequestRef.current?.abort();
  }, [scope.kind, scope.kind === 'global' ? '' : scope.projectId, scope.kind === 'episode' ? scope.episodeId : '']);
  const createAttemptScope = `asset-create:${scopeKey(scope)}:${kind}`;
  const createDirty = Object.values(fields(draft)).some((value) => Array.isArray(value) ? value.length > 0 : !!value);
  const selectedDirty = !!selected && !!selectedSaved && JSON.stringify(fields(selected)) !== JSON.stringify(fields(selectedSaved));
  const editorBlocked = detailLoading || !!detailError || editorConflict;
  const savingSelected = useRef<Promise<LibraryAssetRead | null> | null>(null);
  const switchingSelected = useRef(false);
  const selectedTrigger = useRef<{ node: HTMLElement | null; id: string } | null>(null);
  const assetEntries = useRef(new Map<string, HTMLButtonElement>());
  const selectedId = useRef<string | null>(selected?.id ?? null);
  selectedId.current = selected?.id ?? null;
  useEffect(() => {
    const requestedId = creation.subject?.type === 'asset' ? creation.subject.id : null;
    if (requestedId !== dismissedSubject.current) dismissedSubject.current = null;
    if (scope.kind !== 'episode' || !requestedId || requestedId === dismissedSubject.current || selected || loading) return;
    const item = items.find(asset => asset.id === requestedId);
    if (item) void openSelected(item);
  }, [scope.kind, creation.subject?.id, creation.subject?.type, selected, loading, items]);
  useLayoutEffect(() => {
    if (!inEpisode || selected || !selectedTrigger.current) return;
    const trigger = selectedTrigger.current;
    const target = trigger.node?.isConnected ? trigger.node : assetEntries.current.get(trigger.id);
    if (!target && loading) return;
    let frame = 0;
    const restore = () => {
      if (selectedId.current || selectedTrigger.current !== trigger) return;
      if (target?.closest('[hidden]')) return;
      target?.focus();
      selectedTrigger.current = null;
      observer?.disconnect();
    };
    // 窄屏关闭编辑后，先等作品区域重新显示，再恢复原入口的键盘焦点。
    const pane = target?.closest('.agent-work-pane');
    const observer = pane ? new MutationObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(restore);
    }) : null;
    if (pane) observer?.observe(pane, { attributes: true, attributeFilter: ['hidden'] });
    frame = requestAnimationFrame(restore);
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); };
  }, [inEpisode, selected, items, loading]);
  const editorBarrier = useRef({ dirty: false, flush: async () => false });
  editorBarrier.current = {
    dirty: selectedDirty || creating && createDirty || busy || generationSubmitting || detailLoading || editorConflict,
    flush: async () => {
      if (creating && createDirty || generationSubmitting || editorBlocked) {
        setNotice('请先完成当前素材的创建或图片操作。'); return false;
      }
      if (savingSelected.current) return !!await savingSelected.current;
      if (busy) return false;
      return !selectedDirty || !!await saveBeforeGenerate();
    },
  };
  useEffect(() => {
    registerBarrier?.({ hasUnsettled: () => editorBarrier.current.dirty, flush: () => editorBarrier.current.flush() });
    return () => registerBarrier?.(null);
  }, [registerBarrier]);
  const imagePresentation = selected ? assetImagePresentation(selected.media_id, selected.image, candidates) : null;

  const loadCandidates = useCallback(async (append = false, reset = false) => {
    if (!selected?.id) return;
    candidateRequestRef.current?.abort();
    const controller = new AbortController();
    candidateRequestRef.current = controller;
    setCandidateLoading(true);
    try {
      const page = await assetLibraries.candidates(selected.id, controller.signal, append ? candidateOffset : 0, 20);
      if (!controller.signal.aborted) {
        setCandidates((current) => reset ? page.items : append
          ? [...current.map((item) => page.items.find((fresh) => fresh.id === item.id) ?? item), ...page.items.filter((item) => !current.some((own) => own.id === item.id))]
          : [...page.items, ...current.filter((item) => !page.items.some((fresh) => fresh.id === item.id))]);
        setCandidateOffset((current) => reset ? page.items.length : append ? current + page.items.length : Math.max(current, page.items.length));        setCandidateTotal(page.total);
        setCandidateError('');
        setCandidateLoaded(true);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setCandidateError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted) setCandidateLoading(false);
    }
  }, [selected?.id, candidateOffset]);

  useEffect(() => {
    if (!selected) return;
    setCandidates([]);
    setCandidateOffset(0);
    setCandidateTotal(0);
    setCandidateLoaded(false); setCandidateError('');
  }, [selected?.id]);

  useEffect(() => {
    if (!selected || detailLoading || detailError) return;
    void loadCandidates(false);
    return () => candidateRequestRef.current?.abort();
  }, [selected?.id, selected?.row_version, detailLoading, detailError]);

  async function changeKind(next: AssetKind) {
    if ((selectedDirty || createDirty) && !await confirmAction('切换分类会放弃当前未保存的素材编辑。确定继续？')) return;
    clearSelected(); setCreating(false); setDraft(blank(next));
    clearAttempt(createAttemptScope, attemptStorage()); setKind(next); setOffset(0); onKindChange?.(next);
  }

  async function closeCreating() {
    if (createDirty && !await confirmAction('放弃尚未保存的新素材？')) return;
    clearAttempt(createAttemptScope, attemptStorage()); setCreating(false); setDraft(blank(kind));
  }

  async function openSelected(item: LibraryAssetRead) {
    if (busy || generationSubmitting || savingSelected.current || operationRef.current || switchingSelected.current) return;
    switchingSelected.current = true;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    try {
      if (selectedDirty && !await confirmAction('切换素材会放弃当前未保存的修改。确定继续？')) return;
      dismissedSubject.current = null;
      selectedTrigger.current = { node: trigger, id: item.id };
      if (scope.kind === 'episode') { creation.selectSubject?.({ type: 'asset', id: item.id, label: `${labels[item.kind]} · ${item.name}`, revision: item.row_version }); creation.revealPanel?.(); }
      selectedId.current = item.id;
      setSelected(item); setSelectedSaved(item); setNotice(''); setCandidates([]); setCandidateTotal(0); setCandidateOffset(0);
      setEditorConflict(false); setConflictReviewOpen(false); conflictRequestRef.current?.abort();
      void loadSelected(item);
    } finally { switchingSelected.current = false; }
  }
  async function closeSelected() {
    if (inEpisode && selectedDirty) { setCloseReviewOpen(true); return; }
    if (selectedDirty && !await confirmAction('放弃尚未保存的素材修改？')) return;
    clearSelected();
  }
  async function saveAndCloseSelected() {
    const saved = await saveBeforeGenerate();
    setCloseReviewOpen(false);
    if (saved) clearSelected();
  }

  function clearSelected() {
    detailRequestRef.current?.abort(); conflictRequestRef.current?.abort();
    // 编辑立即关闭时，路由移除参数可能仍在过渡中；先阻止旧选择再次打开详情。
    dismissedSubject.current = selectedId.current;
    selectedId.current = null;
    setSelected(null); setSelectedSaved(null); setDetailLoading(false); setDetailError('');
    setEditorConflict(false); setConflictReviewOpen(false); setConflictLatest(null);
    if (scope.kind === 'episode') creation.selectSubject?.(null);
  }

  async function loadSelected(item: LibraryAssetRead) {
    detailRequestRef.current?.abort();
    const controller = new AbortController(); detailRequestRef.current = controller;
    setDetailLoading(true); setDetailError('');
    try {
      const remote = await assetLibraries.detail(item.id, controller.signal);
      if (controller.signal.aborted || detailRequestRef.current !== controller || selectedId.current !== item.id) return;
      const next = { ...item, ...remote };
      setSelected(next); setSelectedSaved(next);
    } catch (cause) {
      if (!controller.signal.aborted && selectedId.current === item.id) setDetailError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted && detailRequestRef.current === controller && selectedId.current === item.id) setDetailLoading(false);
    }
  }

  function markConflict(cause: unknown) {
    if (cause instanceof ApiError && (cause.code === 'asset_version_conflict' || cause.status === 409 && cause.code === 'HTTP_409')) {
      setEditorConflict(true); setConflictLatest(null);
    }
  }

  function downloadDraft() {
    if (!selected) return;
    const content = JSON.stringify({ asset_id: selected.id, scope, row_version: selectedSaved?.row_version, media_id: selectedSaved?.media_id, draft: fields(selected) }, null, 2);
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `asset-${selected.id}-draft.json`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function reviewConflict() {
    if (!selected) return;
    conflictRequestRef.current?.abort();
    const controller = new AbortController(); conflictRequestRef.current = controller;
    const id = selected.id;
    setConflictReviewOpen(true); setConflictLoading(true); setConflictLatest(null); setConflictError('');
    try {
      const remote = await assetLibraries.detail(id, controller.signal);
      if (!controller.signal.aborted && conflictRequestRef.current === controller && selectedId.current === id) setConflictLatest(remote);
    } catch (cause) {
      if (!controller.signal.aborted && selectedId.current === id) setConflictError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted && conflictRequestRef.current === controller && selectedId.current === id) setConflictLoading(false);
    }
  }

  function resolveConflict(keepDraft: boolean) {
    if (!selected || !conflictLatest || conflictLoading || conflictLatest.id !== selected.id) return;
    const latest = { ...selected, ...conflictLatest };
    setSelected(keepDraft ? { ...latest, ...fields(selected) } : latest); setSelectedSaved(latest);
    setEditorConflict(false); setConflictReviewOpen(false); setConflictLatest(null); setCandidateError('');
    setNotice(keepDraft ? '已核对最新版本，草稿仍保留。请检查后保存。' : '已明确放弃本页草稿，载入最新素材。');
    refresh();
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (busy || !draft.name.trim()) return;
    setBusy(true); setNotice('');
    try {
      const body = { ...draft, name: draft.name.trim() };
      const idempotencyKey = await requestAttempt(createAttemptScope, body, attemptStorage());
      await assetLibraries.create(scope, body, idempotencyKey);
      clearAttempt(createAttemptScope, attemptStorage()); setCreating(false); setDraft(blank(kind)); refresh();
    } catch (cause) { setNotice(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  function save(confirmShared = false): Promise<LibraryAssetRead | null> {
    if (savingSelected.current) return savingSelected.current;
    const pending = saveSelected(confirmShared);
    savingSelected.current = pending;
    void pending.finally(() => { if (savingSelected.current === pending) savingSelected.current = null; });
    return pending;
  }
  async function saveSelected(confirmShared = false): Promise<LibraryAssetRead | null> {
    if (!selected || busy || editorBlocked) return null;
    setBusy(true); setNotice('');
    try {
      const next = await assetLibraries.update(selected.id, assetPatch({ row_version: selected.row_version, ...fields(selected) }, confirmShared));
      const merged = { ...selected, ...next };
      if (selectedId.current === selected.id) { setSelected(merged); setSelectedSaved(merged); setNotice('素材文字已保存。'); }
      refresh();
      return merged;
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'shared_asset_confirmation_required' && !confirmShared
        && await confirmAction('此素材被多处引用，保存会同步影响所有引用。确定继续？')) {
        setBusy(false); return await saveSelected(true);
      }
      markConflict(cause); setNotice(errorMessage(cause)); return null;
    } finally { setBusy(false); }
  }

  async function saveBeforeGenerate(): Promise<LibraryAssetRead | null> {
    if (!selected || operationRef.current || editorBlocked) return null;
    operationRef.current = true;
    try {
      if (!selectedDirty) return selectedSaved ?? selected;
      return await save();
    } finally {
      operationRef.current = false;
    }
  }
  async function remove(item: LibraryAssetRead) {
    if (busy || !await confirmAction(`从“${title}”移除 ${item.name}？素材本体及其他库引用仍会保留。`)) return;
    setBusy(true);
    try { await assetLibraries.unlink(scope, item.id, item.row_version); if (creation.subject?.id === item.id) creation.selectSubject?.(null); refresh(); }
    catch (cause) { setNotice(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  function closeImports() {
    importRequestRef.current?.abort();
    setImportLoading(false); setShowImport(false);
  }

  async function openImports() {
    if (!importFrom) return;
    importRequestRef.current?.abort();
    const controller = new AbortController();
    importRequestRef.current = controller;
    setImports([]); setImportQuery(''); setShowImport(true); setImportLoading(true); setNotice(''); setImportNoticeType('info');
    try {
      const [source, members] = await Promise.all([
        loadAllLibrary(importFrom, kind, controller.signal),
        copiesOnImport ? Promise.resolve([]) : loadAllLibrary(scope, kind, controller.signal),
      ]);
      const memberIds = new Set(members.map(item => item.id));
      if (!controller.signal.aborted) setImports(source.filter(item => !memberIds.has(item.id)));
    } catch (cause) { if (!controller.signal.aborted) { setImportNoticeType('error'); setNotice(errorMessage(cause)); } }
    finally { if (!controller.signal.aborted) setImportLoading(false); }
  }

  async function link(id: string) {
    setBusy(true); setImportNoticeType('info'); setNotice('');
    try {
      if (copiesOnImport && scope.kind === 'project') {
        const payload = { source_type: 'asset', source_id: id };
        const attemptScope = `import:${scope.projectId}:${id}`;
        const key = await requestAttempt(attemptScope, payload, attemptStorage());
        const job = (await http.post(`/projects/${scope.projectId}/imports`, payload, { headers: { 'Idempotency-Key': key } })).data;
        setNotice('正在复制素材和媒体文件，请稍候…');
        let complete = false;
        for (let attempt = 0; attempt < 30; attempt += 1) {
          const state = (await http.get(`/projects/${scope.projectId}/imports/${job.id}`)).data;
          if (state.status === 'succeeded') { complete = true; clearAttempt(attemptScope, attemptStorage()); break; }
          if (state.status === 'failed' || state.status === 'cancelled') { clearAttempt(attemptScope, attemptStorage()); setImportNoticeType('error'); setNotice('复制未完成，请检查项目权限与存储服务后重试。'); return; }
          await new Promise(resolve => window.setTimeout(resolve, 1000));
        }
        if (!complete) { setNotice('复制仍在进行，请稍后刷新。再次选择此素材会继续核对同一次导入。'); refresh(); return; }
      } else await assetLibraries.link(scope, id);
      setNotice(''); setShowImport(false); refresh();
    }
    catch (cause) { setImportNoticeType('error'); setNotice(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function share(item: LibraryAssetRead) {
    if (!shareTo || busy) return;
    setBusy(true); setNotice('');
    try { await assetLibraries.link(shareTo, item.id); setNotice(`“${item.name}”已共享到项目库。`); }
    catch (cause) { setNotice(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function upload(file: File) {
    if (!selected) return false;
    if (file.size > 20 * 1024 * 1024) { setCandidateError('图片不能超过 20 MiB。'); return false; }
    setBusy(true); setCandidateError('');
    try { const candidate = await assetLibraries.upload(selected.id, file); setCandidates((list) => [candidate, ...list.filter((item) => item.id !== candidate.id)]); }
    catch (cause) { setCandidateError(errorMessage(cause)); }
    finally { setBusy(false); }
    return false;
  }

  async function addMedia(mediaId: string): Promise<boolean> {
    if (!selected || busy || generationSubmitting) return false;
    setBusy(true);
    try { const candidate = await assetLibraries.addCandidate(selected.id, mediaId); setCandidates((list) => [candidate, ...list.filter((item) => item.id !== candidate.id)]); setCandidateError(''); return true; }
    catch (cause) { setCandidateError(errorMessage(cause)); return false; }
    finally { setBusy(false); }
  }

  async function confirm(candidate: Pick<AssetCandidate, 'media_id'>, ensureCandidate = false) {
    if (!selected || busy || generationSubmitting || adoptionRef.current || editorBlocked) return;
    adoptionRef.current = true;
    if (!await confirmAction('确认采用此图？候选仍会保留，可稍后改选。')) { adoptionRef.current = false; return; }
    setBusy(true); setCandidateError('');
    let confirmShared = false;
    let acknowledgeStale = false;
    try {
      let saved: LibraryAssetRead;
      while (true) {
        try {
          if (selectedDirty) {
            const updated = await assetLibraries.update(selected.id, assetPatch({ row_version: selected.row_version, ...fields(selected) }, confirmShared));
            saved = { ...selected, ...updated };
            setSelected(saved); setSelectedSaved(saved);
          } else {
            saved = selectedSaved ?? selected;
          }
          break;
        } catch (cause) {
          if (cause instanceof ApiError && cause.code === 'shared_asset_confirmation_required' && !confirmShared
            && await confirmAction('保存当前素材文字会影响共享引用，确定继续？')) {
            confirmShared = true;
            continue;
          }
          throw cause;
        }
      }
      if (ensureCandidate) await assetLibraries.addCandidate(saved.id, candidate.media_id);
      while (true) {
        try {
          const next = await assetLibraries.confirm(saved.id, {
            ...assetConfirmRequest(saved, candidate.media_id, confirmShared),
            ...(acknowledgeStale ? { acknowledge_stale_source: true } : {}),
          });
          const merged = { ...saved, ...next };
          setSelected(merged); setSelectedSaved(merged); refresh();
          void loadCandidates(false);
          break;
        } catch (cause) {
          if (cause instanceof ApiError && cause.code === 'shared_asset_confirmation_required' && !confirmShared
            && await confirmAction('采用图片会影响共享引用，确定继续？')) {
            confirmShared = true;
            continue;
          }
          if (cause instanceof ApiError && (cause.code === 'stale_source' || cause.code === 'stale_generation_source') && !acknowledgeStale
            && await confirmAction('这张图片基于素材的旧内容生成。请核对图片，仍要明确采用吗？')) {
            acknowledgeStale = true;
            continue;
          }
          throw cause;
        }
      }
    } catch (cause) {
      markConflict(cause); setCandidateError(errorMessage(cause));
    } finally {
      adoptionRef.current = false;
      setBusy(false);
    }
  }
  return <section className="overview-card remote-asset-library" aria-label={title}>
    <div className="overview-heading"><div><h2>{title}</h2></div><div>{toolbar}{importFrom && !readOnly && <Button disabled={busy} onClick={() => void openImports()}>从{importLibrary}库{copiesOnImport ? '复制' : '添加'}</Button>}{!readOnly && <Button type="primary" icon={<Icon name="plus" size={16}/>} disabled={busy} onClick={() => { setNotice(''); setCreating(true); }}>新建{labels[kind]}</Button>}</div></div>
    <div className="resource-toolbar"><Segmented aria-label="素材类别" value={kind} onChange={changeKind} options={(Object.keys(labels) as AssetKind[]).map((value) => ({ value, label: labels[value] }))}/><span className="asset-library-count" aria-live="polite">{loading ? '正在载入…' : `共 ${total} 个${labels[kind]}`}</span><Input.Search value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} aria-label="搜索素材" placeholder={`搜索${labels[kind]}名称或描述`} allowClear/></div>
    {!readOnly && <BatchLauncher scope={{ library: scope.kind, ...(scope.kind !== 'global' ? { project_id: scope.projectId } : {}), ...(scope.kind === 'episode' ? { episode_id: scope.episodeId } : {}), asset_kind: kind, search: query.trim() }} selection={batchSelection} loadedIds={items.map(item => item.id)} disabled={busy || loading || creating || !!selected}/>}
    {notice && <Alert type="info" showIcon message={notice}/>} {error && <Alert type="error" showIcon message={error} action={<Button onClick={refresh}>重试</Button>}/>}
    {loading ? <Skeleton title paragraph={{ rows: 3 }}/> : items.length ? <div className="asset-grid">{items.map((item) => inEpisode ? <EpisodeAssetCard key={item.id} asset={item} selected={selected?.id === item.id} disabled={busy || generationSubmitting} readOnly={readOnly} canShare={!!shareTo} batchEnabled={batchSelection.enabled} batchChecked={batchSelection.ids.includes(item.id)} entryRef={node => { if (node) assetEntries.current.set(item.id, node); else assetEntries.current.delete(item.id); }} onOpen={() => void openSelected(item)} onEdit={() => void openSelected(item)} onShare={() => void share(item)} onRemove={() => void remove(item)} onBatchChange={checked => batchSelection.toggle(item.id, checked)}/> : <article className="asset-card library-resource-card" key={item.id}>
      {batchSelection.enabled && !readOnly && <Checkbox className="batch-item-select" aria-label={`批量选择 ${item.name}`} checked={batchSelection.ids.includes(item.id)} onChange={event => batchSelection.toggle(item.id, event.target.checked)}>批量选择</Checkbox>}
      <>{item.image?.url ? <PreviewImage triggerClassName="resource-image" src={item.image.url} alt={item.name}/> : <button type="button" className="resource-image" aria-label={`查看 ${item.name} 的详情`} onClick={() => openSelected(item)}><span><Icon name={item.kind === 'character' ? 'person' : item.kind} size={28}/>暂无图片</span></button>}</>
      <div className="asset-card-content"><h3><button type="button" className="asset-name-button" ref={node => { if (node) assetEntries.current.set(item.id, node); else assetEntries.current.delete(item.id); }} disabled={busy || generationSubmitting} onClick={() => openSelected(item)}>{item.name}</button></h3><p>{item.description || item.prompt || '补充外观或特征，方便后续创作。'}</p><div className="resource-meta"><span className={`status-badge ${item.state === 'confirmed' ? 'is-success' : 'is-pending'}`}>{item.state === 'confirmed' ? '已确认' : '待确认'}</span><small>{item.reference_count} 处引用</small></div></div>
      <div className="resource-card-actions"><Button type="link" onClick={() => openSelected(item)}>{readOnly ? '查看详情' : '编辑素材'}</Button>{shareTo && !readOnly && <Button type="link" disabled={busy} onClick={() => void share(item)}>共享到项目</Button>}{!readOnly && <Button type="link" danger disabled={busy} onClick={() => void remove(item)}>移除</Button>}</div>
    </article>)}</div> : !error && <div className="studio-empty"><h3>{query ? `没有找到匹配的${labels[kind]}` : `添加${labels[kind]}素材`}</h3><p>{query ? '试试其他关键词，或清空搜索。' : '添加文字描述和参考图，后续分镜可以直接关联使用。'}</p>{query ? <Button onClick={() => { setQuery(''); setOffset(0); }}>清空搜索</Button> : !readOnly && <Button onClick={() => { setNotice(''); setCreating(true); }}>新建{labels[kind]}</Button>}</div>}
    <Pagination current={Math.floor(offset / 20) + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={(page) => setOffset((page - 1) * 20)}/>
    {legacyDownload && <details className="legacy-tools"><summary>旧版草稿</summary><p>如需找回旧版浏览器中的素材记录，可下载备份。</p><Button onClick={legacyDownload}>下载浏览器旧稿</Button></details>}

    {creating && <Dialog title={`新建${labels[kind]}`} className="asset-editor-drawer asset-create-drawer" canClose={!busy} onClose={closeCreating}><form onSubmit={create}><div className="asset-create-body"><p className="episode-help">先保存文字信息，再上传参考图或生成图片。</p><AssetFields className="asset-editor-fields" value={draft} disabled={busy} onChange={setDraft}/>{notice && <Alert type="error" showIcon message={notice}/>}</div><div className="asset-editor-drawer-footer"><span role="status">{createDirty ? '尚未创建' : '名称必填，其他信息可稍后补充'}</span><Button disabled={busy} onClick={closeCreating}>取消</Button><Button type="primary" htmlType="submit" loading={busy} disabled={!draft.name.trim()}>创建{labels[kind]}</Button></div></form></Dialog>}
    {showImport && <Dialog title={`从${importLibrary}素材库${copiesOnImport ? '复制' : '添加'}`} className="asset-import-dialog" canClose={!busy} onClose={closeImports}><div className="asset-import-search"><Input.Search aria-label="搜索可添加素材" placeholder="搜索名称或描述" value={importQuery} allowClear onChange={event => setImportQuery(event.target.value)}/></div><div className="asset-import-body">{notice && <Alert type={importNoticeType} message={notice}/>} {importLoading ? <Skeleton paragraph={{ rows: 3 }}/> : imports.filter(item => !importQuery.trim() || (item.name + item.description).toLocaleLowerCase().includes(importQuery.trim().toLocaleLowerCase())).map((item) => <div className="resource-import-row" key={item.id}><div><strong>{item.name}</strong><p>{item.description}</p></div><Button disabled={busy} onClick={() => void link(item.id)}>{copiesOnImport ? '复制到项目' : '添加到本库'}</Button></div>)}{!importLoading && !notice && !imports.length && <p>此分类暂无可添加的素材，可以先在当前库新建。</p>}{!importLoading && !!imports.length && !imports.some(item => !importQuery.trim() || (item.name + item.description).toLocaleLowerCase().includes(importQuery.trim().toLocaleLowerCase())) && <p className="episode-help">没有找到匹配素材，请换个关键词。</p>}</div><div className="production-dialog-footer"><span>{copiesOnImport ? '素材与媒体创建独立副本，后续修改分别保存。' : auth.enabled ? '同一项目内引用同一素材，修改会同步。' : '添加后共享同一素材，修改会同步。'}</span><Button disabled={busy} onClick={closeImports}>关闭</Button></div></Dialog>}
    {selected && <DetailFrame title={`${readOnly ? '查看' : '编辑'} ${selected.name}`} className="asset-editor-drawer" canClose={!busy && !generationSubmitting} onClose={closeSelected}>
      <div className="asset-editor-drawer-body">
        <div className="asset-editor-meta" aria-label="素材状态">
          <span>{labels[selected.kind]}</span>
          <span className={`status-badge ${selected.state === 'confirmed' ? 'is-success' : 'is-pending'}`}>{selected.state === 'confirmed' ? '已确认' : '待确认'}</span>
          <small>{selected.reference_count} 处引用</small>
        </div>

        {detailLoading ? <div role="status"><p>正在读取最新素材…</p><Skeleton active paragraph={{ rows: 4 }}/></div> : detailError ? <Alert type="error" showIcon message={detailError} action={<Button onClick={() => void loadSelected(selected)}>重新读取素材</Button>}/> : <>
        {editorConflict && <Alert type="warning" showIcon message="素材已在其他页面修改。本页草稿已保留，请核对最新版本后再保存、生成或采用图片。" action={<><Button disabled={busy || generationSubmitting} onClick={() => void reviewConflict()}>核对最新版本</Button><Button onClick={downloadDraft}>下载素材草稿</Button></>}/>}
        <nav className="asset-editor-section-nav" aria-label="素材编辑区域">
          <Button onClick={() => document.getElementById('asset-editor-details-title')?.scrollIntoView({ block: 'start' })}>素材信息</Button>
          <Button onClick={() => document.getElementById('asset-editor-media-title')?.scrollIntoView({ block: 'start' })}>图片与生成</Button>
        </nav>
        <div className="asset-editor-columns"><section className="asset-editor-section" aria-labelledby="asset-editor-details-title">
          <div className="asset-editor-section-heading">
            <div><h3 id="asset-editor-details-title">素材信息</h3><p>整理可复用的文字特征，后续分镜会沿用这些内容。</p></div>
          </div>
          <AssetFields className="asset-editor-fields" value={selected} disabled={readOnly || busy || generationSubmitting} onChange={(change) => setSelected({ ...selected, ...change })}/>
          {notice && !editorConflict && (
            <Alert type="info" showIcon message={notice}/>
          )}
            {imagePresentation?.current && <figure className="asset-current-image">
              <div className="asset-image-frame"><PreviewImage src={imagePresentation.current.url ?? ''} alt={`${selected.name}当前采用`}/><span>当前采用</span></div>
              <figcaption>当前视觉形象{!readOnly && selected.state === 'unconfirmed' && selected.media_id && <Button disabled={busy || generationSubmitting || editorBlocked} onClick={() => void confirm({ media_id: selected.media_id! }, true)}>重新确认当前图片</Button>}</figcaption>
            </figure>}

        </section>

        <section className="asset-editor-section asset-editor-media" aria-labelledby="asset-editor-media-title">
          <div className="asset-editor-section-heading">
            <div><h3 id="asset-editor-media-title">图片与生成</h3><p>上传或选择图片，确认采用后作为后续分镜的视觉依据。</p></div>
          </div>
          <ReferenceImages kind="asset" ownerId={selected.id} version={selected.row_version} disabled={readOnly || busy || generationSubmitting || editorBlocked} beforeChange={saveBeforeGenerate} onBusyChange={setGenerationSubmitting} onChanged={async () => {
            const remote = await assetLibraries.detail(selected.id);
            setSelected(current => current?.id === remote.id ? { ...current, ...remote } : current);
            setSelectedSaved(current => current?.id === remote.id ? { ...current, ...remote } : current); refresh();
          }}/>
          <AssetImageGeneration
            asset={selected}
            scope={scope}
            readOnly={readOnly}
            disabled={editorBlocked || busy}
            onSaveBeforeGenerate={saveBeforeGenerate}
            onCandidatesChanged={() => void loadCandidates(false)}
            onSubmissionBusyChange={setGenerationSubmitting}
            onVersionConflict={markConflict}
          />
          {selected.kind === 'character' && scope.kind !== 'global' && <CharacterVoicePanel key={`${scope.projectId}:${selected.id}`} projectId={scope.projectId} characterId={selected.id} disabled={readOnly || busy || generationSubmitting || editorBlocked}/>}
          {!readOnly && <div className="asset-editor-media-actions">
            <Upload disabled={busy || generationSubmitting || editorBlocked} accept="image/png,image/jpeg,image/webp" showUploadList={false} beforeUpload={upload}><Button loading={busy} disabled={editorBlocked}>上传图片</Button></Upload>
            <Button disabled={busy || generationSubmitting || editorBlocked} onClick={() => setShowImages(true)}>从资产库选择</Button>
          </div>}
          <p className="asset-generation-note">支持 PNG、JPEG、WebP，最大 20 MB。候选不会自动采用。</p>
          {candidateLoading && !candidateLoaded && <div role="status"><p>正在读取图片候选…</p><Skeleton active paragraph={{ rows: 2 }}/></div>}
          {candidateError && <Alert type="error" showIcon message={candidateError} action={<Button loading={candidateLoading} onClick={() => void loadCandidates(false)}>重试读取图片候选</Button>}/>}
          {candidateLoaded && !candidateLoading && !candidateError && !imagePresentation?.alternatives.length && <p className="episode-help">{imagePresentation?.current ? '暂无其他候选图片。' : '暂无候选图片，可上传、选择或生成后核对采用。'}</p>}
          {!!imagePresentation?.alternatives.length && <div className="asset-image-gallery">
            {imagePresentation.alternatives.length > 0 && <div className="asset-image-alternatives">
              <h4>{imagePresentation.current ? '其他候选' : '待选图片'}</h4>
              <div className="image-candidate-grid">{imagePresentation.alternatives.map((candidate) => <article className="image-candidate" key={candidate.id}>
                <PreviewImage triggerClassName="asset-candidate-preview" src={candidate.url} alt={selected.name + '候选'} previewTitle="候选图片预览"/>
                {candidate.generation && <small>{candidate.generation.is_stale ? '素材已修改，请核对后明确采用' : '生成任务 ' + candidate.generation.generation_id}</small>}
                <Button disabled={readOnly || busy || generationSubmitting || editorBlocked} onClick={() => void confirm(candidate)}>确认采用</Button>
              </article>)}</div>
              {candidates.length < candidateTotal && <Button loading={candidateLoading} disabled={candidateLoading} onClick={() => void loadCandidates(true)}>加载更多候选</Button>}
            </div>}
          </div>}
        </section></div></>}
      </div>
      <div className="asset-editor-drawer-footer">
        <span role="status">{detailLoading ? '正在读取…' : detailError ? '素材读取失败' : editorConflict ? '版本冲突，草稿已保留' : busy ? '正在保存…' : generationSubmitting ? '正在提交图片操作…' : selectedDirty ? '有未保存的修改' : '所有修改已保存'}</span><Button disabled={busy || generationSubmitting} onClick={closeSelected}>{selectedDirty ? '取消' : '关闭'}</Button>
        {!readOnly && <Button type="primary" aria-label="保存修改" loading={busy || generationSubmitting} disabled={editorBlocked || !selectedDirty || !selected.name.trim() || generationSubmitting} onClick={() => void save()}>保存修改</Button>}
      </div>
      {conflictReviewOpen && <Dialog title="核对素材版本" className="asset-import-dialog" onClose={() => { conflictRequestRef.current?.abort(); setConflictReviewOpen(false); }}>
        <div className="asset-import-body"><p>读取最新版本不会改变本页草稿。核对文字与当前图片后，明确决定如何继续。</p>{conflictLoading ? <div role="status"><p>正在读取最新版本…</p><Skeleton active paragraph={{ rows: 3 }}/></div> : conflictError ? <Alert type="error" showIcon message={conflictError} action={<Button onClick={() => void reviewConflict()}>重试读取最新版本</Button>}/> : conflictLatest && <><section><h4>本页草稿</h4><AssetVersionSummary asset={selected}/></section><section><h4>服务端最新版本</h4><AssetVersionSummary asset={conflictLatest}/></section></>}</div>
        <div className="production-dialog-footer"><Button autoFocus data-dialog-autofocus onClick={() => { conflictRequestRef.current?.abort(); setConflictReviewOpen(false); }}>稍后核对</Button><Button onClick={downloadDraft}>下载素材草稿</Button><Button danger disabled={conflictLoading || !conflictLatest} onClick={() => resolveConflict(false)}>放弃草稿并载入最新版本</Button><Button type="primary" disabled={conflictLoading || !conflictLatest} onClick={() => resolveConflict(true)}>已核对，保留草稿继续编辑</Button></div>
      </Dialog>}
    </DetailFrame>}
    {closeReviewOpen && selected && <Dialog title="未保存的修改" className="studio-confirm-dialog" canClose={!busy && !generationSubmitting} onClose={() => setCloseReviewOpen(false)}>
      <div className="studio-confirm-content"><span className="studio-confirm-icon"><Icon name="warning" size={22}/></span><p>“{selected.name}”有未保存的修改。保存后返回，或明确放弃本次修改。</p></div>
      <div className="dialog-actions"><Button autoFocus data-dialog-autofocus disabled={busy || generationSubmitting} onClick={() => setCloseReviewOpen(false)}>取消</Button><Button danger disabled={busy || generationSubmitting} onClick={() => { setCloseReviewOpen(false); clearSelected(); }}>放弃修改</Button><Button type="primary" loading={busy} disabled={editorBlocked || generationSubmitting || !selected.name.trim()} onClick={() => void saveAndCloseSelected()}>保存并返回</Button></div>
    </Dialog>}
    {showImages && selected && <ImagePicker busy={busy} projectId={scope.kind === 'global' ? undefined : scope.projectId} onClose={() => setShowImages(false)} onSelect={addMedia}/>}
  </section>;
}

function EpisodeAssetDetail({ title, onClose, canClose, children }: ComponentProps<typeof Dialog>) {
  return <EpisodeEditorSlot stage="assets"><section className="episode-asset-detail" aria-label={title}>
    <header><h3>{title}</h3><Button disabled={!canClose} onClick={onClose}>返回素材列表</Button></header>
    {children}
  </section></EpisodeEditorSlot>;
}

function AssetVersionSummary({ asset }: { asset: AssetRead }) {
  const values = [['名称', asset.name], ['分类', asset.label], ['描述', asset.description], ['提示词描述', asset.prompt], ['标签', asset.tags.join('，')], ...(asset.kind === 'scene' ? [['场景时间', asset.scene_time]] : [])];
  return <><dl className="generation-facts">{values.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || '未填写'}</dd></div>)}</dl>{asset.image?.url && <PreviewImage triggerClassName="asset-candidate-preview" src={asset.image.url} alt={`${asset.name}当前图片`}/>}</>;
}

function AssetFields({ value, disabled, onChange, className = '' }: { value: AssetDraft | LibraryAssetRead; disabled: boolean; onChange: (value: any) => void; className?: string }) {
  const set = (patch: object) => onChange({ ...value, ...patch });
  return <div className={`studio-form ${className}`.trim()}><label className="asset-field-name">名称<Input maxLength={255} value={value.name} disabled={disabled} onChange={(event) => set({ name: event.target.value })}/></label><label className="asset-field-label">分类<Input maxLength={120} value={value.label} disabled={disabled} onChange={(event) => set({ label: event.target.value })}/></label><label className="asset-field-description">描述<Input.TextArea autoSize={{ minRows: 3, maxRows: 8 }} value={value.description} disabled={disabled} onChange={(event) => set({ description: event.target.value })}/></label><label className="asset-field-prompt">提示词描述<Input.TextArea autoSize={{ minRows: 4, maxRows: 10 }} value={value.prompt} disabled={disabled} onChange={(event) => set({ prompt: event.target.value })}/></label><label className="asset-field-tags">标签<Input value={value.tags.join('，')} disabled={disabled} onChange={(event) => set({ tags: event.target.value.split(/[，,]/) })}/></label>{value.kind === 'scene' && <label className="asset-field-scene-time">场景时间<Input value={value.scene_time} disabled={disabled} onChange={(event) => set({ scene_time: event.target.value })}/></label>}</div>;
}
