import { projectAssetsApi } from '@host/api/creation/assets/management';
import { ApiError, errorMessage } from '@host/api/http';
import { LocalAssetEditor, ProjectAssetEditor } from '@host/features/creation/assets/editor/ProjectAssetEditor';
import { remapAssetPromptReferences } from '@host/features/creation/assets/images/asset-prompt-references';
import { ProjectAssetCard } from '@host/features/creation/assets/list/ProjectAssetCard';
import { useAssetLibrary } from '@host/features/creation/assets/list/useAssetLibrary';
import {
  AssetEditingSession,
  assetFields,
  assetRecoveryKey,
  assetRecoveryStorage,
} from '@host/features/creation/assets/sessions/asset-editing-session';
import { blankAssetDraft, localAssetDraftKey } from '@host/features/creation/assets/sessions/local-asset-draft-model';
import { LocalAssetSession } from '@host/features/creation/assets/sessions/local-asset-session';
import { creationScope, saveRecovery } from '@host/features/creation/shared/recovery/creation-recovery';
import { ResourceViewToggle, useResourceView } from '@host/features/creation/shared/resource-view/ResourceViewToggle';
import {
  getStandardSaveSnapshot,
  releaseStandardSession,
  retainStandardSession,
} from '@host/features/creation/shared/sessions/registry';
import { CreationEditorSlot, useCreationControls } from '@host/features/creation/workspace/layout/CreationWorkspace';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import { confirmAction } from '@host/shared/ui/confirm';
import type { ProjectAssetScope } from '@host/types/creation/assets/editor/ProjectAssetWorkspace';
import type { AssetKind, LibraryAssetRead } from '@host/types/creation/assets/management';
import type { LocalAssetDraft } from '@host/types/creation/assets/sessions/local-asset-draft-model';
import type { CreationDraftValue } from '@host/types/creation/drafts/requests';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import { Button, Input, Pagination, Skeleton, Spin } from 'antd';
import { Image as ImageIcon, Plus, Search, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import '../list/index.css';
const labels = { all: '全部', character: '角色', scene: '场景', prop: '道具' };

export function ProjectAssetWorkspace({
  scope,
  title,
  readOnly = false,
  toolbar,
  refreshToken = 0,
  registerBarrier,
}: {
  scope: ProjectAssetScope;
  title: string;
  readOnly?: boolean;
  toolbar?: ReactNode;
  refreshToken?: number;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  const auth = useAuth();
  const actor = auth.user?.id ?? (auth.enabled ? 'signed-out' : 'anonymous');
  return (
    <ScopedProjectAssets
      key={`${actor}:${scope.projectId}`}
      {...{
        scope,
        title,
        readOnly,
        toolbar,
        refreshToken,
        registerBarrier,
        actor,
      }}
    />
  );
}

function ScopedProjectAssets({
  scope,
  title,
  readOnly,
  toolbar,
  refreshToken,
  registerBarrier,
  actor,
}: {
  scope: ProjectAssetScope;
  title: string;
  readOnly: boolean;
  toolbar?: ReactNode;
  refreshToken: number;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
  actor: string;
}) {
  const creation = useCreationControls();
  const [view, setView] = useResourceView('assets');
  const [kind, setKind] = useState<AssetKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const { items, total, loading, error, refresh: refreshItems } = useAssetLibrary(scope, kind, query, offset);
  const [categoryCounts, setCategoryCounts] = useState<Record<AssetKind | 'all', number> | null>(null);
  const [countError, setCountError] = useState('');
  const [countRevision, setCountRevision] = useState(0);
  const refresh = useCallback(() => {
    refreshItems();
    setCountRevision((value) => value + 1);
  }, [refreshItems]);
  useEffect(() => {
    const controller = new AbortController();
    const kinds = ['all', 'character', 'scene', 'prop'] as const;
    setCountError('');
    Promise.all(
      kinds.map(async (category) => {
        const page = await projectAssetsApi.list(
          scope,
          { kind: category === 'all' ? undefined : category, limit: 1 },
          controller.signal,
        );
        return [category, page.total] as const;
      }),
    )
      .then((entries) => {
        if (!controller.signal.aborted)
          setCategoryCounts(Object.fromEntries(entries) as Record<AssetKind | 'all', number>);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setCountError(`读取素材分类数量失败：${errorMessage(cause)}`);
      });
    return () => controller.abort();
  }, [scope.projectId, countRevision]);
  const recoveryKey = localAssetDraftKey(actor, scope.projectId);
  const localResource = `asset-new:${scope.projectId}`;
  const localSession = useMemo(
    () =>
      retainStandardSession(
        actor,
        localResource,
        () =>
          new LocalAssetSession(
            recoveryKey,
            {
              create: (body, key) => projectAssetsApi.create(scope, body, key),
              preserveCreated: (saved, current) => {
                if (current.generation)
                  saveRecovery(creationScope(`generation-draft:${scope.projectId}:${saved.id}:asset_image`, actor), {
                    row_version: null,
                    value: current.generation,
                  });
                const storage = assetRecoveryStorage(assetRecoveryKey(actor, scope.projectId, saved.id));
                if (
                  JSON.stringify(assetFields(saved)) !==
                  JSON.stringify(
                    assetFields({
                      ...current,
                      kind: current.kind ?? saved.kind,
                    }),
                  )
                ) {
                  storage.save({
                    base: saved,
                    fields: assetFields({
                      ...current,
                      kind: current.kind ?? saved.kind,
                    }),
                  });
                  const resource = `asset:${saved.id}`;
                  retainStandardSession(
                    actor,
                    resource,
                    () =>
                      new AssetEditingSession(saved, projectAssetsApi.update, storage, 1000, projectAssetsApi.detail),
                    {
                      label: `素材 · ${current.name}`,
                      projectId: scope.projectId,
                    },
                  );
                  releaseStandardSession(actor, resource);
                }
              },
            },
            readOnly,
          ),
        { label: '新素材', projectId: scope.projectId },
      ),
    [actor, localResource, recoveryKey, readOnly, scope.projectId],
  );
  const localState = useSyncExternalStore(localSession.subscribe, localSession.getSnapshot);
  const { drafts, recoveryError } = localState;
  useEffect(() => () => releaseStandardSession(actor, localResource), [localSession, actor, localResource]);
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selection = useRef(selectedId);
  selection.current = selectedId;
  const [detail, setDetail] = useState<LibraryAssetRead | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [overrides, setOverrides] = useState<Record<string, LibraryAssetRead>>({});
  const countedVersions = useRef(new Map<string, { kind: AssetKind; rowVersion: string }>());
  const [privateInitial, setPrivateInitial] = useState<Record<string, CreationDraftValue>>({});
  const [notice, setNotice] = useState<{
    type: 'warning' | 'error';
    message: string;
    assetId: string;
  } | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const removing = useRef<string | null>(null);
  const removedIds = useRef(new Set<string>());
  const entries = useRef(new Map<string, HTMLButtonElement>());
  const requests = useRef<{
    detail?: AbortController;
    import?: AbortController;
  }>({});
  const alive = useRef(true);
  const switching = useRef(false);
  const requestedSubject = useRef<string | null>(null);
  const editorBarrier = useRef<NavigationBarrier | null>(null);
  const registerEditor = useCallback((barrier: NavigationBarrier | null) => {
    editorBarrier.current = barrier;
  }, []);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      requests.current.detail?.abort();
      requests.current.import?.abort();
    };
  }, [recoveryKey]);
  useEffect(() => {
    if (!refreshToken) return;
    setOverrides({});
    refresh();
    const id = selection.current;
    if (id && !id.startsWith('draft:')) void readDetail(id);
  }, [refreshToken, refresh]);
  useEffect(() => {
    registerBarrier?.({
      hasUnsettled: () => !!editorBarrier.current?.hasUnsettled() || localSession.hasUnsettled(),
      hasMemoryOnlyChanges: () =>
        localSession.hasMemoryOnlyChanges() || !!editorBarrier.current?.hasMemoryOnlyChanges?.(),
      flush: async () => {
        void editorBarrier.current?.flush();
        return localSession.flush();
      },
    });
    return () => registerBarrier?.(null);
  }, [registerBarrier, localSession]);
  function keepDrafts(next: LocalAssetDraft[]) {
    localSession.replace(next);
    draftsRef.current = next;
  }
  function editDraft(id: string, patch: Partial<LocalAssetDraft>) {
    localSession.edit(id, patch);
    draftsRef.current = localSession.getSnapshot().drafts;
  }
  const localSelected = drafts.find((draft) => draft.id === selectedId);
  const known = items.filter((item) => !removedIds.current.has(item.id)).map((item) => overrides[item.id] ?? item);
  const visibleDrafts = drafts.filter(
    (item) =>
      (kind === 'all' || item.kind === kind) &&
      (!query.trim() || `${item.name}${item.description}`.includes(query.trim())),
  );

  const receiveArtwork = useCallback(
    (asset: LibraryAssetRead) => {
      if (!alive.current || removedIds.current.has(asset.id)) return;
      setOverrides((current) => ({ ...current, [asset.id]: asset }));
      const counted = countedVersions.current.get(asset.id);
      if (!counted || counted.rowVersion !== asset.row_version) {
        countedVersions.current.set(asset.id, {
          kind: asset.kind,
          rowVersion: asset.row_version,
        });
        if (counted && counted.kind !== asset.kind) refresh();
      }
      if (selection.current === asset.id) {
        creation.selectSubject?.({
          type: 'asset',
          id: asset.id,
          label: `${labels[asset.kind]} · ${asset.name}`,
          revision: asset.row_version,
        });
      }
    },
    [creation.selectSubject, refresh],
  );

  async function selectAsset(id: string, seed?: LibraryAssetRead, reveal = true) {
    if (switching.current || removing.current || id === selection.current) return;
    switching.current = true;
    try {
      if (editorBarrier.current?.hasUnsettled()) void editorBarrier.current.flush();
      requests.current.detail?.abort();
      selection.current = id;
      setSelectedId(id);
      setDetailError('');
      if (reveal) creation.revealPanel?.();
      if (id.startsWith('draft:')) {
        setDetail(null);
        setDetailLoading(false);
        creation.selectSubject?.(null);
        return;
      }
      if (seed)
        creation.selectSubject?.({
          type: 'asset',
          id,
          label: `${labels[seed.kind]} · ${seed.name}`,
          revision: seed.row_version,
        });
      void readDetail(id, seed);
    } finally {
      switching.current = false;
    }
  }
  async function readDetail(id: string, seed?: LibraryAssetRead) {
    requests.current.detail?.abort();
    const controller = new AbortController();
    requests.current.detail = controller;
    setDetail(null);
    setDetailLoading(true);
    setDetailError('');
    try {
      const remote = await projectAssetsApi.detail(id, controller.signal);
      if (controller.signal.aborted || selection.current !== id || !alive.current) return;
      const asset = { link_id: null, position: null, ...seed, ...remote };
      setDetail(asset);
      receiveArtwork(asset);
    } catch (cause) {
      if (!controller.signal.aborted && alive.current) setDetailError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted && alive.current) setDetailLoading(false);
    }
  }
  useEffect(() => {
    const requested = creation.subject?.type === 'asset' ? creation.subject.id : null;
    const changed = requested !== requestedSubject.current;
    requestedSubject.current = requested;
    const activeId = selection.current;
    if (activeId && !activeId.startsWith('draft:') && requests.current.detail?.signal.aborted) {
      void readDetail(
        activeId,
        known.find((item) => item.id === activeId),
      );
      return;
    }
    if (changed && requested && requested !== selection.current) {
      void selectAsset(
        requested,
        known.find((item) => item.id === requested),
        false,
      );
      return;
    }
    if (!selection.current && !requested && !loading && !error && !recoveryError) {
      const pendingDraft = visibleDrafts.find((draft) => draft.status !== 'local');
      if (pendingDraft) void selectAsset(pendingDraft.id, undefined, false);
      else if (known[0]) void selectAsset(known[0].id, known[0], false);
      else if (visibleDrafts[0]) void selectAsset(visibleDrafts[0].id, undefined, false);
    }
  }, [
    creation.subject?.type,
    creation.subject?.id,
    selectedId,
    loading,
    items,
    drafts,
    kind,
    query,
    error,
    recoveryError,
  ]);

  async function addDraft() {
    if (readOnly || recoveryError || switching.current || removing.current) return;
    if (editorBarrier.current?.hasUnsettled()) void editorBarrier.current.flush();
    const draft = blankAssetDraft(kind);
    keepDrafts([...draftsRef.current, draft]);
    setQuery('');
    selection.current = draft.id;
    setSelectedId(draft.id);
    setDetail(null);
    setDetailError('');
    setDetailLoading(false);
    creation.selectSubject?.(null);
    creation.revealPanel?.();
    requestAnimationFrame(() => entries.current.get(draft.id)?.scrollIntoView({ block: 'nearest' }));
  }
  const createdSeen = useRef(new Set<string>());
  useEffect(() => {
    for (const [id, result] of Object.entries(localState.created)) {
      if (createdSeen.current.has(id)) continue;
      createdSeen.current.add(id);
      if (result.generation)
        setPrivateInitial((value) => ({
          ...value,
          [result.asset.id]: result.generation!,
        }));
      setOverrides((value) => ({ ...value, [result.asset.id]: result.asset }));
      if (selection.current === id) {
        selection.current = result.asset.id;
        setSelectedId(result.asset.id);
        setDetail(result.asset);
        receiveArtwork(result.asset);
      }
      refresh();
    }
  }, [localState.created, receiveArtwork, refresh]);
  const createDraft = (id: string) => localSession.create(id);
  async function removeDraft(id: string) {
    if (readOnly || removing.current) return;
    const draft = draftsRef.current.find((item) => item.id === id);
    if (!draft || ['saving', 'unknown'].includes(draft.status)) return;
    removing.current = id;
    try {
      if (
        !(await confirmAction(`删除本机素材草稿“${draft.name || '未命名素材'}”？草稿内容将被清除，无法恢复。`, {
          title: '删除素材草稿',
          confirmText: '删除草稿',
          className: 'standard-creation-dialog',
        }))
      )
        return;
      setRemovingId(id);
      setNotice(null);
      const current = draftsRef.current.find((item) => item.id === id);
      if (!current || ['saving', 'unknown'].includes(current.status)) {
        setNotice({
          type: 'warning',
          assetId: id,
          message: '素材创建正在处理或结果待核对，未删除草稿。请先核对原创建请求。',
        });
        return;
      }
      if (selection.current === id && editorBarrier.current?.hasUnsettled() && !(await editorBarrier.current.flush())) {
        setNotice({
          type: 'warning',
          assetId: id,
          message: '素材操作尚未完成，未删除草稿。请等待上传完成后重试。',
        });
        return;
      }
      keepDrafts(draftsRef.current.filter((item) => item.id !== id));
      if (selection.current === id) {
        selection.current = null;
        setSelectedId(null);
        creation.selectSubject?.(null);
      }
    } finally {
      removing.current = null;
      if (alive.current) setRemovingId(null);
    }
  }
  async function removeSaved(asset: LibraryAssetRead) {
    if (readOnly || removing.current) return;
    removing.current = asset.id;
    let session: AssetEditingSession | null = null;
    try {
      if (
        !(await confirmAction(
          `删除素材“${asset.name}”？素材将从当前项目中移除，无法恢复；已被分镜引用的素材需先解除关联。`,
          {
            title: '删除素材',
            confirmText: '删除素材',
            className: 'standard-creation-dialog',
          },
        ))
      )
        return;
      setRemovingId(asset.id);
      setNotice(null);
      if (
        selection.current === asset.id &&
        editorBarrier.current?.hasUnsettled() &&
        !(await editorBarrier.current.flush())
      ) {
        setNotice({
          type: 'warning',
          assetId: asset.id,
          message: '素材仍有未完成或未能保存的操作，未删除。输入已保留，请从保存详情处理后重试。',
        });
        return;
      }
      const generationSave = getStandardSaveSnapshot().find(
        (entry) => entry.key === `generation-draft:${scope.projectId}:${asset.id}:asset_image`,
      );
      if (generationSave && !['saved', 'idle'].includes(generationSave.status) && !(await generationSave.flush?.())) {
        setNotice({
          type: 'warning',
          assetId: asset.id,
          message: '素材的生成设置尚未保存或存在版本冲突，未删除。私人输入已保留，请从保存详情处理后重试。',
        });
        return;
      }
      const current = overrides[asset.id] ?? asset;
      session = retainStandardSession(
        actor,
        `asset:${asset.id}`,
        () =>
          new AssetEditingSession(
            current,
            projectAssetsApi.update,
            assetRecoveryStorage(
              assetRecoveryKey(actor, scope.projectId, asset.id),
              `short-drama:asset-editor:v1:${actor}:${scope.projectId}:${asset.id}`,
            ),
            1000,
            projectAssetsApi.detail,
          ),
        { label: `素材 · ${current.name}`, projectId: scope.projectId },
      );
      const saved = await session.flush();
      if (!saved) {
        setNotice({
          type: 'warning',
          assetId: asset.id,
          message: '素材编辑尚未保存或存在版本冲突，未删除。草稿已保留，请核对后重试。',
        });
        return;
      }
      await projectAssetsApi.unlink(scope, asset.id, saved.row_version);
      if (!alive.current) return;
      removedIds.current.add(asset.id);
      if (selection.current === asset.id) {
        requests.current.detail?.abort();
        selection.current = null;
        setSelectedId(null);
        setDetail(null);
        setDetailError('');
        setDetailLoading(false);
        creation.selectSubject?.(null);
      }
      if (offset > 0 && known.length === 1) setOffset((value) => Math.max(0, value - 20));
      refresh();
    } catch (cause) {
      if (alive.current)
        setNotice({
          type: cause instanceof ApiError && cause.status === 409 ? 'warning' : 'error',
          assetId: asset.id,
          message: `删除素材失败：${errorMessage(cause)}。素材与当前输入已保留。`,
        });
    } finally {
      if (session) releaseStandardSession(actor, `asset:${asset.id}`);
      removing.current = null;
      if (alive.current) setRemovingId(null);
    }
  }

  const editor = localSelected ? (
    <LocalAssetEditor
      draft={localSelected}
      readOnly={readOnly || !!recoveryError || !!removingId}
      registerBarrier={registerEditor}
      onUploadBusyChange={(busy) => localSession.setUploadBusy(localSelected.id, busy)}
      onChange={(patch) => editDraft(localSelected.id, patch)}
      onReferencesChange={(ids, mapping) => {
        const current = localSession.getSnapshot().drafts.find((item) => item.id === localSelected.id);
        if (current)
          editDraft(current.id, {
            generation: {
              model_config_id: null,
              image_settings: null,
              video_settings: null,
              ...current.generation,
              input_text: remapAssetPromptReferences(
                current.generation?.input_text ?? '',
                mapping.before,
                mapping.after,
              ),
              reference_media_ids: ids,
            },
          });
      }}
      onRetry={() => void createDraft(localSelected.id)}
      onRemove={() => void removeDraft(localSelected.id)}
    />
  ) : detail && detail.id === selectedId ? (
    <ProjectAssetEditor
      key={detail.id}
      asset={detail}
      scope={scope}
      actor={actor}
      readOnly={readOnly || !!removingId}
      initialGeneration={privateInitial[detail.id]}
      registerBarrier={registerEditor}
      onArtwork={receiveArtwork}
      onRemove={() => void removeSaved(overrides[detail.id] ?? detail)}
    />
  ) : (
    <section className="standard-object-panel standard-asset-empty" aria-label="素材工作区">
      {detailLoading ? (
        <div className="standard-asset-workspace-state" role="status">
          <Spin />
          <strong>正在加载作品</strong>
        </div>
      ) : detailError ? (
        <div className="standard-asset-workspace-state" role="alert">
          <TriangleAlert size={30} />
          <strong>作品加载失败</strong>
          <p>{detailError}</p>
          <Button onClick={() => selectedId && void readDetail(selectedId)}>重新读取素材</Button>
        </div>
      ) : (
        <div className="standard-asset-workspace-state">
          <ImageIcon size={30} />
          <strong>选择一个对象开始制作</strong>
          {!readOnly && <Button onClick={() => void addDraft()}>新增素材</Button>}
        </div>
      )}
    </section>
  );

  return (
    <section className={`standard-assets-list resource-view-${view}`} aria-label={title}>
      <header className="standard-assets-heading">
        <h2>
          <span className="standard-assets-title-text">资产库</span>{' '}
          <small>{categoryCounts ? categoryCounts.all + drafts.length : '—'}</small>
        </h2>
        <div className="standard-assets-tools">
          <ResourceViewToggle value={view} onChange={setView} />
          {toolbar}
        </div>
      </header>
      <div className="standard-assets-filters">
        <Input
          prefix={<Search size={15} aria-hidden="true" />}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOffset(0);
          }}
          aria-label="搜索素材"
          placeholder="搜索名称或描述"
          allowClear
        />
        <div className="standard-asset-category-tabs" aria-label="素材类别">
          {Object.entries(labels).map(([value, label]) => (
            <button
              type="button"
              key={value}
              aria-label={label}
              aria-pressed={kind === value}
              onClick={() => {
                setKind(value as AssetKind | 'all');
                setOffset(0);
              }}
            >
              <span>{label}</span>
              <small>
                {categoryCounts
                  ? categoryCounts[value as AssetKind | 'all'] +
                    drafts.filter((item) => value === 'all' || item.kind === value).length
                  : '—'}
              </small>
            </button>
          ))}
        </div>
      </div>
      {countError && (
        <StudioFeedback
          type="error"
          title="素材分类"
          noticeKey={`asset-counts`}
          message={countError}
          actions={
            <Button size="small" onClick={() => setCountRevision((value) => value + 1)}>
              重试读取分类数量
            </Button>
          }
        />
      )}
      {notice && (
        <StudioFeedback
          type={notice.type}
          title="删除素材"
          noticeKey={`asset-remove:${notice.assetId}`}
          message={notice.message}
          actions={
            <Button
              onClick={() => {
                refresh();
                if (selection.current === notice.assetId && !notice.assetId.startsWith('draft:'))
                  void readDetail(notice.assetId);
              }}
            >
              重新读取素材
            </Button>
          }
        />
      )}
      {recoveryError && (
        <StudioFeedback
          type="error"
          title="素材恢复记录"
          noticeKey={`asset-recovery`}
          message={recoveryError}
          actions={
            <>
              <Button
                onClick={() => {
                  let content = JSON.stringify(draftsRef.current, null, 2);
                  try {
                    content = localStorage.getItem(recoveryKey) ?? content;
                  } catch {
                    /* The current in-memory draft can still be exported. */
                  }
                  const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
                  const link = document.createElement('a');
                  link.href = url;
                  link.download = `project-${scope.projectId}-asset-drafts.json`;
                  link.click();
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}
              >
                下载本机恢复记录
              </Button>
              <Button
                onClick={async () => {
                  if (
                    !(await confirmAction('明确清除本机素材恢复记录？此操作不会删除服务端素材。', {
                      className: 'standard-creation-dialog',
                    }))
                  )
                    return;
                  localSession.discardRecovery();
                }}
              >
                清除本机恢复记录
              </Button>
            </>
          }
        />
      )}
      <div className="standard-assets-scroll" aria-busy={loading}>
        {error &&
          (known.length ? (
            <StudioFeedback
              type="error"
              title="素材列表"
              noticeKey={`asset-list`}
              message={error}
              actions={<Button onClick={refresh}>重新加载素材</Button>}
            />
          ) : (
            <div className="standard-assets-list-state" role="alert">
              <TriangleAlert size={30} />
              <strong>列表加载失败</strong>
              <p>{error}</p>
              <Button onClick={refresh}>重新加载素材</Button>
            </div>
          ))}
        {loading && !known.length ? (
          <div role="status" aria-label="正在加载素材列表">
            {[0, 1, 2].map((index) => (
              <div className="standard-asset-skeleton" key={index}>
                <Skeleton.Image active />
                <Skeleton active title={false} paragraph={{ rows: 1 }} />
              </div>
            ))}
          </div>
        ) : (
          known.map((item) => (
            <ProjectAssetCard
              key={item.id}
              asset={item}
              selected={selectedId === item.id}
              disabled={!!removingId}
              readOnly={readOnly}
              removing={removingId === item.id}
              entryRef={(node) => {
                if (node) entries.current.set(item.id, node);
                else entries.current.delete(item.id);
              }}
              onOpen={() => void selectAsset(item.id, item)}
              onRemove={() => void removeSaved(item)}
            />
          ))
        )}
        {visibleDrafts.map((item) => (
          <ProjectAssetCard
            key={item.id}
            asset={item}
            selected={selectedId === item.id}
            disabled={!!removingId}
            readOnly={readOnly || !!recoveryError}
            removing={removingId === item.id}
            entryRef={(node) => {
              if (node) entries.current.set(item.id, node);
              else entries.current.delete(item.id);
            }}
            onOpen={() => void selectAsset(item.id)}
            onRemove={() => void removeDraft(item.id)}
          />
        ))}
        {!loading && !error && !known.length && !visibleDrafts.length && (
          <div className="standard-assets-list-state">
            <ImageIcon size={30} />
            <strong>{query ? '没有匹配的条目' : '还没有资产'}</strong>
            <p>{query ? '试试其他关键词或分类。' : '点击下方创建，添加角色、场景或道具。'}</p>
          </div>
        )}
        <Pagination
          size="small"
          current={Math.floor(offset / 20) + 1}
          pageSize={20}
          total={total}
          hideOnSinglePage
          showSizeChanger={false}
          onChange={(page) => setOffset((page - 1) * 20)}
        />
      </div>
      <Button
        className="resource-create-button"
        type="text"
        aria-label="创建资产"
        icon={<Plus size={16} />}
        disabled={readOnly || !!recoveryError || !!removingId}
        onClick={() => void addDraft()}
      >
        创建
      </Button>
      <CreationEditorSlot stage="assets">{editor}</CreationEditorSlot>
    </section>
  );
}
