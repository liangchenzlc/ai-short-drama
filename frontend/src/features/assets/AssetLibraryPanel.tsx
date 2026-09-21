import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Input, Pagination, Select, Spin, Upload } from 'antd';
import { ApiError, errorMessage } from '../../api/http';
import { assetLibraries, type AssetCandidate, type AssetDraft, type AssetKind, type AssetScope, type LibraryAssetRead } from '../../api/modules/assets';
import { Dialog } from '../../components/ui/Dialog';
import { useAssetLibrary } from './useAssetLibrary';
import { assetConfirmRequest, assetPatch } from '../projects/workflow-contract';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';

const labels: Record<AssetKind, string> = { character: '角色', scene: '场景', prop: '道具' };
const blank = (kind: AssetKind): AssetDraft => ({ kind, name: '', label: '', description: '', prompt: '', tags: [], scene_time: '' });
const scopeKey = (scope: AssetScope) => scope.kind === 'global' ? 'global' : scope.kind === 'project' ? `project:${scope.projectId}` : `episode:${scope.projectId}:${scope.episodeId}`;
const fields = (asset: AssetDraft | LibraryAssetRead) => ({ name: asset.name, label: asset.label, description: asset.description, prompt: asset.prompt, tags: asset.tags, scene_time: asset.scene_time });

async function loadAllCandidates(id: string, signal: AbortSignal) {
  const items: AssetCandidate[] = []; let offset = 0; let total = 1;
  while (offset < total) { const page = await assetLibraries.candidates(id, signal, offset); items.push(...page.items); total = page.total; offset += page.items.length; if (!page.items.length) break; }
  return items;
}

async function loadAllLibrary(scope: AssetScope, kind: AssetKind) {
  const items: LibraryAssetRead[] = []; let offset = 0; let total = 1;
  while (offset < total) { const page = await assetLibraries.list(scope, { kind, offset, limit: 100 }); items.push(...page.items); total = page.total; offset += page.items.length; if (!page.items.length) break; }
  return items;
}

export function AssetLibraryPanel({
  scope, title, readOnly = false, importFrom, shareTo, legacyDownload, initialKind = 'character',
}: {
  scope: AssetScope;
  title: string;
  readOnly?: boolean;
  importFrom?: AssetScope;
  shareTo?: AssetScope;
  legacyDownload?: () => void;
  initialKind?: AssetKind;
}) {
  const [kind, setKind] = useState<AssetKind>(initialKind);
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const { items, total, loading, error, refresh } = useAssetLibrary(scope, kind, query, offset);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<AssetDraft>(() => blank(kind));
  const [selected, setSelected] = useState<LibraryAssetRead | null>(null);
  const [selectedSaved, setSelectedSaved] = useState<LibraryAssetRead | null>(null);
  const [candidates, setCandidates] = useState<AssetCandidate[]>([]);
  const [candidateError, setCandidateError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [imports, setImports] = useState<LibraryAssetRead[]>([]);
  const [showImport, setShowImport] = useState(false);
  const createAttemptScope = `asset-create:${scopeKey(scope)}:${kind}`;
  const createDirty = Object.values(fields(draft)).some((value) => Array.isArray(value) ? value.length > 0 : !!value);
  const selectedDirty = !!selected && !!selectedSaved && JSON.stringify(fields(selected)) !== JSON.stringify(fields(selectedSaved));

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setCandidateError('');
    loadAllCandidates(selected.id, controller.signal)
      .then((loaded) => { if (!controller.signal.aborted) setCandidates(loaded); })
      .catch((cause) => { if (!controller.signal.aborted) setCandidateError(errorMessage(cause)); });
    return () => controller.abort();
  }, [selected?.id, selected?.row_version]);

  function changeKind(next: AssetKind) {
    if ((selectedDirty || createDirty) && !window.confirm('切换分类会放弃当前未保存的素材编辑。确定继续？')) return;
    setSelected(null); setSelectedSaved(null); setCreating(false); setDraft(blank(next));
    clearAttempt(createAttemptScope, attemptStorage()); setKind(next); setOffset(0);
  }

  function closeCreating() {
    if (createDirty && !window.confirm('放弃尚未保存的新素材？')) return;
    clearAttempt(createAttemptScope, attemptStorage()); setCreating(false); setDraft(blank(kind));
  }

  function openSelected(item: LibraryAssetRead) { setSelected(item); setSelectedSaved(item); setNotice(''); }
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

  async function remove(item: LibraryAssetRead) {
    if (busy || !window.confirm(`从“${title}”移除 ${item.name}？素材本体及其他库引用仍会保留。`)) return;
    setBusy(true);
    try { await assetLibraries.unlink(scope, item.id, item.row_version); refresh(); }
    catch (cause) { setNotice(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function openImports() {
    if (!importFrom) return;
    setShowImport(true); setBusy(true); setNotice('');
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

  async function addMedia() {
    if (!selected) return;
    const mediaId = window.prompt('输入已永久保存的图片媒体 ID');
    if (!mediaId?.trim()) return;
    setBusy(true);
    try { const candidate = await assetLibraries.addCandidate(selected.id, mediaId.trim()); setCandidates((list) => [candidate, ...list.filter((item) => item.id !== candidate.id)]); }
    catch (cause) { setCandidateError(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function confirm(candidate: AssetCandidate, confirmShared = false, acknowledged = false) {
    if (!selected || busy || (!acknowledged && !window.confirm('确认采用此图？候选仍会保留，可稍后改选。'))) return;
    setBusy(true); setCandidateError('');
    try {
      const saved = await assetLibraries.update(selected.id, assetPatch({ row_version: selected.row_version, ...fields(selected) }, confirmShared));
      const next = await assetLibraries.confirm(selected.id, assetConfirmRequest(saved, candidate.media_id, confirmShared));
      const merged = { ...selected, ...next };
      setSelected(merged); setSelectedSaved(merged); refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'shared_asset_confirmation_required' && !confirmShared
        && window.confirm('采用图片会影响共享引用，确定继续？')) {
        setBusy(false); return confirm(candidate, true, true);
      }
      setCandidateError(errorMessage(cause));
    } finally { setBusy(false); }
  }

  return <section className="overview-card remote-asset-library" aria-label={title}>
    <div className="overview-heading"><div><h2>{title}</h2><p>服务端素材库 · 共 {total} 项</p></div><div>{legacyDownload && <Button onClick={legacyDownload}>下载浏览器旧稿</Button>}{importFrom && !readOnly && <Button onClick={() => void openImports()}>从上级库添加</Button>}{!readOnly && <Button type="primary" onClick={() => setCreating(true)}>新建{labels[kind]}</Button>}</div></div>
    <div className="resource-toolbar"><Select value={kind} onChange={changeKind} options={(Object.keys(labels) as AssetKind[]).map((value) => ({ value, label: labels[value] }))}/><Input.Search value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} placeholder="搜索名称、描述、分类或标签" allowClear/></div>
    {notice && <Alert type="info" showIcon message={notice}/>} {error && <Alert type="error" showIcon message={error} action={<Button onClick={refresh}>重试</Button>}/>}
    {loading ? <Spin/> : items.length ? <div className="asset-grid">{items.map((item) => <article className="asset-card" key={item.id}>
      <div className="asset-cover">{item.image?.url ? <img src={item.image.url} alt=""/> : labels[item.kind]}</div>
      <div className="asset-card-content"><h3>{item.name}</h3><p>{item.description || item.prompt || '暂无描述'}</p><small>{item.label || '未分类'} · {item.state === 'confirmed' ? '已确认' : '待确认'} · {item.reference_count} 处引用</small><small className="generation-id">{item.id}</small></div>
      <div><Button type="link" onClick={() => openSelected(item)}>编辑 / 图片</Button>{shareTo && !readOnly && <Button type="link" onClick={() => void share(item)}>共享到项目</Button>}{!readOnly && <Button type="link" danger onClick={() => void remove(item)}>移除</Button>}</div>
    </article>)}</div> : <p className="overview-empty">此库还没有{labels[kind]}。</p>}
    <Pagination current={Math.floor(offset / 20) + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={(page) => setOffset((page - 1) * 20)}/>

    {creating && <Dialog title={`新建${labels[kind]}`} canClose={!busy} onClose={closeCreating}><form onSubmit={create}><AssetFields value={draft} disabled={busy} onChange={setDraft}/><div className="dialog-actions"><Button onClick={closeCreating}>取消</Button><Button type="primary" htmlType="submit" loading={busy}>保存到服务端</Button></div></form></Dialog>}
    {showImport && <Dialog title="从上级素材库添加" canClose={!busy} onClose={() => setShowImport(false)}>{imports.map((item) => <div className="resource-import-row" key={item.id}><div><strong>{item.name}</strong><p>{item.description}</p></div><Button loading={busy} onClick={() => void link(item.id)}>添加</Button></div>)}{!imports.length && <p>暂无可添加素材。</p>}</Dialog>}
    {selected && <Dialog title={`编辑 ${selected.name}`} canClose={!busy} onClose={closeSelected}>
      <AssetFields value={selected} disabled={readOnly || busy} onChange={(change) => setSelected({ ...selected, ...change })}/>
      {notice && <Alert type="info" message={notice}/>}<div className="dialog-actions">{!readOnly && <Button type="primary" loading={busy} disabled={!selectedDirty} onClick={() => void save()}>保存文字</Button>}</div>
      <hr/><div className="overview-heading"><h3>图片候选</h3>{!readOnly && <div><Upload accept="image/png,image/jpeg,image/webp" showUploadList={false} beforeUpload={upload}><Button loading={busy}>上传图片</Button></Upload><Button onClick={() => void addMedia()}>选择已有图片</Button></div>}</div>
      {candidateError && <Alert type="error" message={candidateError}/>}<div className="asset-grid">{candidates.map((candidate) => <article className="asset-card" key={candidate.id}><img src={candidate.url} alt={`${selected.name}候选`}/><Button type={selected.media_id === candidate.media_id ? 'primary' : 'default'} disabled={readOnly || busy || selected.media_id === candidate.media_id} onClick={() => void confirm(candidate)}>{selected.media_id === candidate.media_id ? '当前采用' : '确认采用'}</Button></article>)}</div>
    </Dialog>}
  </section>;
}

function AssetFields({ value, disabled, onChange }: { value: AssetDraft | LibraryAssetRead; disabled: boolean; onChange: (value: any) => void }) {
  const set = (patch: object) => onChange({ ...value, ...patch });
  return <div className="studio-form"><label>名称<Input value={value.name} disabled={disabled} onChange={(event) => set({ name: event.target.value })}/></label><label>分类<Input value={value.label} disabled={disabled} onChange={(event) => set({ label: event.target.value })}/></label><label>描述<Input.TextArea rows={3} value={value.description} disabled={disabled} onChange={(event) => set({ description: event.target.value })}/></label><label>提示词描述<Input.TextArea rows={3} value={value.prompt} disabled={disabled} onChange={(event) => set({ prompt: event.target.value })}/></label><label>标签<Input value={value.tags.join('，')} disabled={disabled} onChange={(event) => set({ tags: event.target.value.split(/[，,]/) })}/></label>{value.kind === 'scene' && <label>场景时间<Input value={value.scene_time} disabled={disabled} onChange={(event) => set({ scene_time: event.target.value })}/></label>}</div>;
}
