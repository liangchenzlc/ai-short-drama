import { PreviewImage } from '../../components/ui/ImagePreview';
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Alert, Button, Input, Pagination, Segmented, Skeleton, Upload } from 'antd';
import { ApiError, errorMessage } from '../../api/http';
import { assetLibraries, type AssetCandidate, type AssetDraft, type AssetKind, type AssetScope, type LibraryAssetRead } from '../../api/modules/assets';
import { Dialog } from '../../components/ui/Dialog';
import { ImagePicker } from '../media-library/ImagePicker';
import { Icon } from '../../components/ui/Icon';
import { useAssetLibrary } from './useAssetLibrary';
import { assetConfirmRequest, assetImagePresentation, assetPatch } from '../projects/workflow-contract';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { ReferenceImages } from '../generations/ReferenceImages';
import { AssetImageGeneration } from './AssetImageGeneration';

const labels: Record<AssetKind, string> = { character: '角色', scene: '场景', prop: '道具' };
const blank = (kind: AssetKind): AssetDraft => ({ kind, name: '', label: '', description: '', prompt: '', tags: [], scene_time: '' });
const scopeKey = (scope: AssetScope) => scope.kind === 'global' ? 'global' : scope.kind === 'project' ? `project:${scope.projectId}` : `episode:${scope.projectId}:${scope.episodeId}`;
const fields = (asset: AssetDraft | LibraryAssetRead) => ({ name: asset.name, label: asset.label, description: asset.description, prompt: asset.prompt, tags: asset.tags, scene_time: asset.scene_time });

async function loadAllLibrary(scope: AssetScope, kind: AssetKind) {
  const items: LibraryAssetRead[] = []; let offset = 0; let total = 1;
  while (offset < total) { const page = await assetLibraries.list(scope, { kind, offset, limit: 100 }); items.push(...page.items); total = page.total; offset += page.items.length; if (!page.items.length) break; }
  return items;
}

export function AssetLibraryPanel({
  scope, title, readOnly = false, importFrom, shareTo, legacyDownload, initialKind = 'character', onKindChange, toolbar, refreshToken = 0,
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
}) {
  const [kind, setKind] = useState<AssetKind>(initialKind);
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const { items, total, loading, error, refresh } = useAssetLibrary(scope, kind, query, offset);
  useEffect(() => { if (refreshToken) refresh(); }, [refreshToken, refresh]);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<AssetDraft>(() => blank(kind));
  const [selected, setSelected] = useState<LibraryAssetRead | null>(null);
  const [selectedSaved, setSelectedSaved] = useState<LibraryAssetRead | null>(null);
  const [candidates, setCandidates] = useState<AssetCandidate[]>([]);
  const [candidateError, setCandidateError] = useState('');
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [candidateOffset, setCandidateOffset] = useState(0);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [generationSubmitting, setGenerationSubmitting] = useState(false);
  const [notice, setNotice] = useState('');
  const [imports, setImports] = useState<LibraryAssetRead[]>([]);
  const [showImport, setShowImport] = useState(false);
  const [showImages, setShowImages] = useState(false);
  const operationRef = useRef(false);
  const adoptionRef = useRef(false);
  const candidateRequestRef = useRef<AbortController | null>(null);
  const createAttemptScope = `asset-create:${scopeKey(scope)}:${kind}`;
  const createDirty = Object.values(fields(draft)).some((value) => Array.isArray(value) ? value.length > 0 : !!value);
  const selectedDirty = !!selected && !!selectedSaved && JSON.stringify(fields(selected)) !== JSON.stringify(fields(selectedSaved));
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
    void loadCandidates(false, true);
    return () => candidateRequestRef.current?.abort();
  }, [selected?.id, selected?.row_version]);

  function changeKind(next: AssetKind) {
    if ((selectedDirty || createDirty) && !window.confirm('切换分类会放弃当前未保存的素材编辑。确定继续？')) return;
    setSelected(null); setSelectedSaved(null); setCreating(false); setDraft(blank(next));
    clearAttempt(createAttemptScope, attemptStorage()); setKind(next); setOffset(0); onKindChange?.(next);
  }

  function closeCreating() {
    if (createDirty && !window.confirm('放弃尚未保存的新素材？')) return;
    clearAttempt(createAttemptScope, attemptStorage()); setCreating(false); setDraft(blank(kind));
  }

  function openSelected(item: LibraryAssetRead) { setSelected(item); setSelectedSaved(item); setNotice(''); setCandidates([]); setCandidateTotal(0); setCandidateOffset(0); }
  function closeSelected() {
    if (selectedDirty && !window.confirm('放弃尚未保存的素材修改？')) return;
    setSelected(null); setSelectedSaved(null);
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

  async function save(confirmShared = false): Promise<LibraryAssetRead | null> {
    if (!selected || busy) return null;
    setBusy(true); setNotice('');
    try {
      const next = await assetLibraries.update(selected.id, assetPatch({ row_version: selected.row_version, ...fields(selected) }, confirmShared));
      const merged = { ...selected, ...next };
      setSelected(merged); setSelectedSaved(merged); refresh(); setNotice('素材文字已保存。');
      return merged;
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'shared_asset_confirmation_required' && !confirmShared
        && window.confirm('此素材被多处引用，保存会同步影响所有引用。确定继续？')) {
        setBusy(false); return save(true);
      }
      setNotice(errorMessage(cause)); return null;
    } finally { setBusy(false); }
  }

  async function saveBeforeGenerate(): Promise<LibraryAssetRead | null> {
    if (!selected || operationRef.current) return null;
    operationRef.current = true;
    try {
      if (!selectedDirty) return selectedSaved ?? selected;
      return await save();
    } finally {
      operationRef.current = false;
    }
  }
  async function remove(item: LibraryAssetRead) {
    if (busy || !window.confirm(`从“${title}”移除 ${item.name}？素材本体及其他库引用仍会保留。`)) return;
    setBusy(true);
    try { await assetLibraries.unlink(scope, item.id, item.row_version); refresh(); }
    catch (cause) { setNotice(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function openImports() {
    if (!importFrom) return;
    setImports([]); setShowImport(true); setBusy(true); setNotice('');
    try { const source = await loadAllLibrary(importFrom, kind); setImports(source.filter((item) => !items.some((own) => own.id === item.id))); }
    catch (cause) { setNotice(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function link(id: string) {
    setBusy(true);
    try { await assetLibraries.link(scope, id); setShowImport(false); refresh(); }
    catch (cause) { setNotice(errorMessage(cause)); }
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

  async function confirm(candidate: AssetCandidate) {
    if (!selected || busy || generationSubmitting || adoptionRef.current) return;
    adoptionRef.current = true;
    if (!window.confirm('确认采用此图？候选仍会保留，可稍后改选。')) { adoptionRef.current = false; return; }
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
            && window.confirm('保存当前素材文字会影响共享引用，确定继续？')) {
            confirmShared = true;
            continue;
          }
          throw cause;
        }
      }
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
            && window.confirm('采用图片会影响共享引用，确定继续？')) {
            confirmShared = true;
            continue;
          }
          if (cause instanceof ApiError && (cause.code === 'stale_source' || cause.code === 'stale_generation_source') && !acknowledgeStale
            && window.confirm('这张图片基于素材的旧内容生成。请核对图片，仍要明确采用吗？')) {
            acknowledgeStale = true;
            continue;
          }
          throw cause;
        }
      }
    } catch (cause) {
      setCandidateError(errorMessage(cause));
    } finally {
      adoptionRef.current = false;
      setBusy(false);
    }
  }
  return <section className="overview-card remote-asset-library" aria-label={title}>
    <div className="overview-heading"><div><h2>{title}</h2><p>保持人物与画面一致，准备好本集要用的素材。</p></div><div>{toolbar}{importFrom && !readOnly && <Button disabled={busy} onClick={() => void openImports()}>从{importFrom.kind === 'project' ? '项目' : '全局'}库添加</Button>}{!readOnly && <Button type="primary" icon={<Icon name="plus" size={16}/>} disabled={busy} onClick={() => { setNotice(''); setCreating(true); }}>新建{labels[kind]}</Button>}</div></div>
    <div className="resource-toolbar"><Segmented aria-label="素材类别" value={kind} onChange={changeKind} options={(Object.keys(labels) as AssetKind[]).map((value) => ({ value, label: labels[value] }))}/><Input.Search value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} aria-label="搜索素材" placeholder={`搜索${labels[kind]}名称或描述`} allowClear/></div>
    {notice && <Alert type="info" showIcon message={notice}/>} {error && <Alert type="error" showIcon message={error} action={<Button onClick={refresh}>重试</Button>}/>}
    {loading ? <Skeleton title paragraph={{ rows: 3 }}/> : items.length ? <div className="asset-grid">{items.map((item) => <article className="asset-card library-resource-card" key={item.id}>
      <>{item.image?.url ? <PreviewImage triggerClassName="resource-image" src={item.image.url} alt={item.name}/> : <button type="button" className="resource-image" aria-label={`查看 ${item.name} 的详情`} onClick={() => openSelected(item)}><span><Icon name={item.kind === 'character' ? 'person' : item.kind} size={32}/>添加参考图</span></button>}</>
      <div className="asset-card-content"><h3>{item.name}</h3><p>{item.description || item.prompt || '补充外观或特征，方便后续创作。'}</p><div className="resource-meta"><span className={`status-badge ${item.state === 'confirmed' ? 'is-success' : 'is-pending'}`}>{item.state === 'confirmed' ? '已确认' : '待确认'}</span><small>{item.reference_count} 处引用</small></div></div>
      <div className="resource-card-actions"><Button type="link" onClick={() => openSelected(item)}>{readOnly ? '查看详情' : '编辑素材'}</Button>{shareTo && !readOnly && <Button type="link" disabled={busy} onClick={() => void share(item)}>共享到项目</Button>}{!readOnly && <Button type="link" danger disabled={busy} onClick={() => void remove(item)}>移除</Button>}</div>
    </article>)}</div> : !error && <div className="studio-empty"><h3>{query ? `没有找到匹配的${labels[kind]}` : `添加${labels[kind]}素材`}</h3><p>{query ? '试试其他关键词，或清空搜索。' : '添加文字描述和参考图，后续分镜可以直接关联使用。'}</p>{query ? <Button onClick={() => { setQuery(''); setOffset(0); }}>清空搜索</Button> : !readOnly && <Button onClick={() => { setNotice(''); setCreating(true); }}>新建{labels[kind]}</Button>}</div>}
    <Pagination current={Math.floor(offset / 20) + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={(page) => setOffset((page - 1) * 20)}/>
    {legacyDownload && <details className="legacy-tools"><summary>旧版草稿</summary><p>如需找回旧版浏览器中的素材记录，可下载备份。</p><Button onClick={legacyDownload}>下载浏览器旧稿</Button></details>}

    {creating && <Dialog title={`新建${labels[kind]}`} className="asset-editor-drawer asset-create-drawer" canClose={!busy} onClose={closeCreating}><form onSubmit={create}><AssetFields value={draft} disabled={busy} onChange={setDraft}/>{notice && <Alert type="error" showIcon message={notice}/>}<div className="dialog-actions"><Button disabled={busy} onClick={closeCreating}>取消</Button><Button type="primary" htmlType="submit" loading={busy} disabled={!draft.name.trim()}>创建{labels[kind]}</Button></div></form></Dialog>}
    {showImport && <Dialog title={`从${importFrom?.kind === 'project' ? '项目' : '全局'}素材库添加`} canClose={!busy} onClose={() => setShowImport(false)}>{notice && <Alert type="error" message={notice}/>} {busy ? <Skeleton paragraph={{ rows: 3 }}/> : imports.map((item) => <div className="resource-import-row" key={item.id}><div><strong>{item.name}</strong><p>{item.description}</p></div><Button disabled={busy} onClick={() => void link(item.id)}>添加到本库</Button></div>)}{!busy && !notice && !imports.length && <p>此分类暂无可添加的素材，可以先在当前库新建。</p>}</Dialog>}
    {selected && <Dialog title={`编辑 ${selected.name}`} className="asset-editor-drawer" canClose={!busy && !generationSubmitting} onClose={closeSelected}>
      <div className="asset-editor-drawer-body">
        <div className="asset-editor-meta" aria-label="素材状态">
          <span>{labels[selected.kind]}</span>
          <span className={`status-badge ${selected.state === 'confirmed' ? 'is-success' : 'is-pending'}`}>{selected.state === 'confirmed' ? '已确认' : '待确认'}</span>
          <small>{selected.reference_count} 处引用</small>
        </div>

        <section className="asset-editor-section" aria-labelledby="asset-editor-details-title">
          <div className="asset-editor-section-heading">
            <div><h3 id="asset-editor-details-title">素材信息</h3><p>整理可复用的文字特征，后续分镜会沿用这些内容。</p></div>
          </div>
          <AssetFields className="asset-editor-fields" value={selected} disabled={readOnly || busy || generationSubmitting} onChange={(change) => setSelected({ ...selected, ...change })}/>
          {notice && (
            <Alert type="info" showIcon message={notice}/>
          )}
        </section>

        <section className="asset-editor-section asset-editor-media" aria-labelledby="asset-editor-media-title">
          <div className="asset-editor-section-heading">
            <div><h3 id="asset-editor-media-title">生成图片</h3><p>上传或选择图片，确认采用后作为后续分镜的视觉依据。</p></div>
          </div>
          <ReferenceImages kind="asset" ownerId={selected.id} version={selected.row_version} disabled={readOnly || busy || generationSubmitting} beforeChange={saveBeforeGenerate} onBusyChange={setGenerationSubmitting} onChanged={async () => {
            const remote = await assetLibraries.detail(selected.id);
            setSelected(current => current ? { ...current, ...remote } : current);
            setSelectedSaved(current => current ? { ...current, ...remote } : current); refresh();
          }}/>
          <AssetImageGeneration
            asset={selected}
            scope={scope}
            readOnly={readOnly}
            onSaveBeforeGenerate={saveBeforeGenerate}
            onCandidatesChanged={() => void loadCandidates(false)}
            onSubmissionBusyChange={setGenerationSubmitting}
          />
          {!readOnly && <div className="asset-editor-media-actions">
            <Upload disabled={busy || generationSubmitting} accept="image/png,image/jpeg,image/webp" showUploadList={false} beforeUpload={upload}><Button loading={busy}>上传图片</Button></Upload>
            <Button disabled={busy || generationSubmitting} onClick={() => setShowImages(true)}>从资产库选择</Button>
          </div>}
          <p className="asset-generation-note">支持 PNG、JPEG、WebP，最大 20 MB。候选不会自动采用。</p>
          {candidateError && <Alert type="error" showIcon message={candidateError}/>}
          {imagePresentation?.visible && <div className="asset-image-gallery">
            {imagePresentation.current && <figure className="asset-current-image">
              <div className="asset-image-frame"><PreviewImage src={imagePresentation.current.url ?? ''} alt={`${selected.name}当前采用`}/><span>当前采用</span></div>
              <figcaption>当前视觉形象</figcaption>
            </figure>}
            {imagePresentation.alternatives.length > 0 && <div className="asset-image-alternatives">
              <h4>{imagePresentation.current ? '其他候选' : '待选图片'}</h4>
              <div className="image-candidate-grid">{imagePresentation.alternatives.map((candidate) => <article className="image-candidate" key={candidate.id}>
                <PreviewImage triggerClassName="asset-candidate-preview" src={candidate.url} alt={selected.name + '候选'} previewTitle="候选图片预览"/>
                {candidate.generation && <small>{candidate.generation.is_stale ? '素材已修改，请核对后明确采用' : '生成任务 ' + candidate.generation.generation_id}</small>}
                <Button disabled={readOnly || busy || generationSubmitting} onClick={() => void confirm(candidate)}>确认采用</Button>
              </article>)}</div>
              {candidates.length < candidateTotal && <Button loading={candidateLoading} disabled={candidateLoading} onClick={() => void loadCandidates(true)}>加载更多候选</Button>}
            </div>}
          </div>}
        </section>
      </div>
      <div className="asset-editor-drawer-footer">
        <Button disabled={busy || generationSubmitting} onClick={closeSelected}>{readOnly ? '关闭' : '取消'}</Button>
        {!readOnly && <Button type="primary" loading={busy || generationSubmitting} disabled={!selectedDirty || generationSubmitting} onClick={() => void save()}>保存修改</Button>}
      </div>
    </Dialog>}
    {showImages && selected && <ImagePicker busy={busy} onClose={() => setShowImages(false)} onSelect={addMedia}/>}
  </section>;
}

function AssetFields({ value, disabled, onChange, className = '' }: { value: AssetDraft | LibraryAssetRead; disabled: boolean; onChange: (value: any) => void; className?: string }) {
  const set = (patch: object) => onChange({ ...value, ...patch });
  return <div className={`studio-form ${className}`.trim()}><label className="asset-field-name">名称<Input value={value.name} disabled={disabled} onChange={(event) => set({ name: event.target.value })}/></label><label className="asset-field-label">分类<Input value={value.label} disabled={disabled} onChange={(event) => set({ label: event.target.value })}/></label><label className="asset-field-description">描述<Input.TextArea rows={4} value={value.description} disabled={disabled} onChange={(event) => set({ description: event.target.value })}/></label><label className="asset-field-prompt">提示词描述<Input.TextArea rows={5} value={value.prompt} disabled={disabled} onChange={(event) => set({ prompt: event.target.value })}/></label><label className="asset-field-tags">标签<Input value={value.tags.join('，')} disabled={disabled} onChange={(event) => set({ tags: event.target.value.split(/[，,]/) })}/></label>{value.kind === 'scene' && <label className="asset-field-scene-time">场景时间<Input value={value.scene_time} disabled={disabled} onChange={(event) => set({ scene_time: event.target.value })}/></label>}</div>;
}
