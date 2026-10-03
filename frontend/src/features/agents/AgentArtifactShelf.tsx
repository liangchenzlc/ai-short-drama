import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Select, Skeleton } from 'antd';
import { agentArtifactsApi } from '../../api/modules/agent-artifacts';
import { agentsApi } from '../../api/modules/agents';
import { assetLibraries } from '../../api/modules/assets';
import { storyboardApi } from '../../api/modules/storyboard';
import { mediaLibrary } from '../../api/modules/media-library';
import { generations } from '../../api/modules/generations';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentArtifact, AgentArtifactDetail, AgentArtifactKind, AgentArtifactOpenRequest, AgentArtifactStatus } from '../../api/types/agent-artifacts';
import type { GenerationDetail, MediaAsset } from '../../api/types/generations';
import type { WritingSession } from '../projects/writing-session';
import { Dialog } from '../../components/ui/Dialog';
import { confirmAction } from '../../components/ui/confirm';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { StoryboardResultPreview } from '../projects/StoryboardResultPreview';
import { reviewParameterLabels } from './agent-events';
import { artifactEffect, artifactFieldLabels, artifactKindLabels, artifactStatusLabels, artifactTarget, artifactVersion, diffValue, nativeArtifact } from './agent-artifact-presentation';

export function AgentArtifactShelf({ projectId, episodeId, schemaReady, readOnly, writingSession, beforeAdopt, onApplied, request, canContinue, onContinue, onOpenExtraction }: {
  projectId: string; episodeId: string; schemaReady: boolean; readOnly: boolean; writingSession: WritingSession;
  beforeAdopt: () => Promise<boolean>; onApplied: () => Promise<boolean>;
  request: AgentArtifactOpenRequest | null; canContinue: (conversationId?: string) => boolean;
  onContinue: () => void; onOpenExtraction: (artifact: AgentArtifactDetail) => void;
}) {
  const api = useMemo(() => agentArtifactsApi(projectId, episodeId), [projectId, episodeId]);
  const shots = useMemo(() => storyboardApi(projectId, episodeId), [projectId, episodeId]);
  const [items, setItems] = useState<AgentArtifact[]>([]);
  const [total, setTotal] = useState(0);
  const [kind, setKind] = useState<AgentArtifactKind>();
  const [status, setStatus] = useState<AgentArtifactStatus>();
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [open, setOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentArtifactDetail | null>(null);
  const [context, setContext] = useState<AgentArtifactOpenRequest | null>(null);
  const [media, setMedia] = useState<MediaAsset | null>(null);
  const [generation, setGeneration] = useState<GenerationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [message, setMessage] = useState('');
  const [accessEnded, setAccessEnded] = useState(false);
  const accessEndedRef = useRef(false);
  const alive = useRef(true); const sequence = useRef(0); const lock = useRef(false);
  const current = useRef({ beforeAdopt, onApplied, canContinue, onContinue, readOnly, schemaReady }); current.current = { beforeAdopt, onApplied, canContinue, onContinue, readOnly, schemaReady };
  useEffect(() => { alive.current = true; return () => { alive.current = false; sequence.current++; }; }, []);
  function endAccess() {
    accessEndedRef.current = true; sequence.current++; setAccessEnded(true);
    setItems([]); setTotal(0); setDetail(null); setDetailId(null); setContext(null); setMedia(null); setGeneration(null); setDetailLoading(false); setMessage('');
  }
  useEffect(() => {
    if (!schemaReady || accessEndedRef.current) { setItems([]); setTotal(0); return; }
    const controller = new AbortController(); setLoading(true); setError('');
    api.list({ offset, kind, status }, controller.signal).then(page => {
      if (!controller.signal.aborted && !accessEndedRef.current) { setItems(old => offset ? [...old, ...page.items.filter(item => !old.some(existing => existing.id === item.id))] : page.items); setTotal(page.total); }
    }).catch(cause => { if (!controller.signal.aborted) {
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) endAccess();
      setError(errorMessage(cause));
    } }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, schemaReady, offset, kind, status, revision]);
  useEffect(() => {
    const refresh = () => { if (!accessEndedRef.current) { setOffset(0); setRevision(value => value + 1); } };
    window.addEventListener('agent-artifacts-updated', refresh);
    return () => window.removeEventListener('agent-artifacts-updated', refresh);
  }, []);
  async function loadDetail(id: string) {
    const intent = ++sequence.current; setDetailLoading(true); setDetailError(''); setMedia(null); setGeneration(null);
    try {
      const next = await api.detail(id);
      if (!alive.current || intent !== sequence.current) return;
      if (next.project_id !== projectId || next.episode_id !== episodeId) throw new Error('候选不属于当前分集。');
      setDetail(next);
      if ((next.kind === 'image_candidate' || next.kind === 'video_candidate') && next.media_asset_id) {
        const [asset, task] = await Promise.all([mediaLibrary.detail(next.media_asset_id), next.generation_task_id ? generations.detail(next.generation_task_id) : Promise.resolve(null)]);
        if (alive.current && intent === sequence.current) { setMedia(asset); setGeneration(task); }
      }
    } catch (cause) { if (alive.current && intent === sequence.current) {
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) endAccess();
      else if (cause instanceof ApiError && cause.status === 404) setDetail(null);
      setDetailError(cause instanceof ApiError ? errorMessage(cause) : cause instanceof Error ? cause.message : '候选暂时无法载入。');
    } } finally { if (alive.current && intent === sequence.current) setDetailLoading(false); }
  }
  function show(id: string, privateContext: AgentArtifactOpenRequest | null = null) {
    if (!schemaReady || accessEndedRef.current || lock.current) return;
    setOpen(true); setDetailId(id); setDetail(null); setContext(privateContext); setMessage(''); void loadDetail(id);
  }
  useEffect(() => { if (request && schemaReady) show(request.id, request); }, [request?.nonce, schemaReady]);
  function close() { if (lock.current) return; sequence.current++; setDetailId(null); setDetail(null); setContext(null); setDetailLoading(false); }
  function refresh(restoreAccess = false) {
    if (restoreAccess) { accessEndedRef.current = false; setAccessEnded(false); }
    if (!accessEndedRef.current) { setOffset(0); setRevision(value => value + 1); }
  }
  function assertAdoptionScope() {
    if (!alive.current || accessEndedRef.current || current.current.readOnly || !current.current.schemaReady) throw new Error('当前创作范围已变化，本次未采用。请重新核对候选。');
  }
  async function continued(artifact: AgentArtifactDetail) {
    if (accessEndedRef.current || !context?.runId || !current.current.canContinue(context.conversationId)) {
      setMessage('候选已采用。返回原对话后，可以核对并继续流程。'); return;
    }
    try {
      if (!await current.current.beforeAdopt()) { setDetailError('候选已采用，但当前作品尚未保存成功。请先处理保存提示，再核对并继续。'); return; }
      if (!alive.current || accessEndedRef.current || !current.current.canContinue(context.conversationId)) { if (alive.current && !accessEndedRef.current) setMessage('候选已采用。返回原对话后，可以核对并继续流程。'); return; }
      await agentsApi.continue(context.runId, artifact.id, artifactVersion(artifact.row_version));
      if (alive.current && !accessEndedRef.current) { setMessage('候选已采用，当前对话已继续。'); current.current.onContinue(); }
    } catch (cause) { if (alive.current && !accessEndedRef.current) setDetailError(`候选已采用，流程尚未确认继续。${errorMessage(cause)} 请核对运行状态后重试继续；无需再次采用。`); }
  }
  async function completed(next: AgentArtifactDetail, resume: boolean) {
    if (!alive.current || accessEndedRef.current) return;
    setDetail(next); refresh();
    const updated = await current.current.onApplied();
    if (!alive.current || accessEndedRef.current) return;
    setMessage(updated ? '已采用到作品。' : '候选已采用。当前页面有新的本地修改，草稿仍保留，请核对后载入最新作品。');
    if (resume) await continued(next);
  }
  async function runAction(action: () => Promise<void>) {
    if (lock.current || readOnly || !schemaReady || accessEndedRef.current) return;
    lock.current = true; setBusy(true); setDetailError(''); setMessage('');
    try { await action(); }
    catch (cause) { if (alive.current) setDetailError(cause instanceof ApiError ? errorMessage(cause) : cause instanceof Error ? cause.message : '操作未完成，请核对候选状态。'); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  async function prepare() {
    if (!await current.current.beforeAdopt()) throw new Error('当前作品尚未保存成功，本次未采用。请处理保存提示后重试。');
    if (!alive.current) throw new Error('当前作品已关闭。');
    const page = await shots.shots(undefined, false, 0, 1);
    assertAdoptionScope();
    return { content_version: artifactVersion(writingSession.getSnapshot().contentVersion), storyboard_version: artifactVersion(page.storyboard_version) };
  }
  async function adopt(resume = false) {
    if (!detail || nativeArtifact(detail.kind) || detail.status !== 'ready') return;
    const artifact = detail;
    await runAction(async () => {
      if (!await confirmAction(`${artifactEffect(artifact.kind)}${resume ? ' 采用后会继续当前对话，可能产生模型费用。' : ''}`, { title: '确认采用候选', confirmText: resume ? '采用并继续' : '确认采用' })) return;
      const versions = await prepare();
      const targetVersion = artifact.target_asset_id ? artifactVersion((await assetLibraries.detail(artifact.target_asset_id)).row_version)
        : artifact.target_shot_id ? artifactVersion((await shots.shot(artifact.target_shot_id)).shot.row_version) : undefined;
      const body = { row_version: artifactVersion(artifact.row_version), ...versions, ...(targetVersion ? { target_row_version: targetVersion } : {}) };
      let next: AgentArtifactDetail;
      assertAdoptionScope();
      try { next = await api.adopt(artifact.id, body); }
      catch (cause) {
        if (!(cause instanceof ApiError) || cause.code !== 'shared_asset_confirmation_required') throw cause;
        if (!alive.current || !await confirmAction(`此素材被 ${cause.details?.reference_count ?? '多'} 处引用。采用会同时影响共享引用，确认继续？`, { title: '确认共享影响', confirmText: '确认共享修改' })) return;
        assertAdoptionScope();
        next = await api.adopt(artifact.id, { ...body, confirm_shared: true });
      }
      await completed(next, resume);
    });
  }
  async function adoptStoryboard(mode: 'append' | 'replace') {
    if (!detail?.generation_task_id || detail.status !== 'ready') return;
    const artifact = detail;
    await runAction(async () => {
      if (!await confirmAction(mode === 'replace' ? '替换会移除当前分镜列表，历史候选保留。确定使用这批分镜？' : '将这批分镜追加到当前列表，确定采用？')) return;
      const versions = await prepare();
      assertAdoptionScope();
      const next = await api.adopt(artifact.id, { row_version: artifactVersion(artifact.row_version), ...versions,
        native_review: { mode, content_version: String(versions.content_version), storyboard_version: String(versions.storyboard_version), confirm_replace: mode === 'replace' } });
      await completed(next, false);
    });
  }
  async function adoptMedia(resume = false) {
    if (!detail || !media?.url || !generation || detail.status !== 'ready') return;
    const artifact = detail;
    await runAction(async () => {
      const versions = await prepare();
      const target = artifact.target_asset_id ? await assetLibraries.detail(artifact.target_asset_id) : artifact.target_shot_id ? (await shots.shot(artifact.target_shot_id)).shot : null;
      if (!target) throw new Error('候选缺少采用对象，请核对来源。');
      const references = 'reference_count' in target && target.reference_count > 1 ? ` 此素材被 ${target.reference_count} 处引用，采用会影响共享引用。` : ' 采用会更新项目共享作品。';
      if (!await confirmAction(`${artifactEffect(artifact.kind)}${references}${resume ? ' 采用后会继续当前对话，可能产生模型费用。' : ''}`, { title: '确认采用媒体', confirmText: resume ? '采用并继续' : '确认采用' })) return;
      assertAdoptionScope();
      const next = await api.adopt(artifact.id, { row_version: artifactVersion(artifact.row_version), ...versions, target_row_version: artifactVersion(target.row_version), confirm_shared: true });
      await completed(next, resume);
    });
  }
  const canResume = !accessEnded && !!context?.runId && current.current.canContinue(context.conversationId);
  const textStale = detail?.status === 'ready' && !nativeArtifact(detail.kind) && String(detail.source_snapshot.content_version) !== writingSession.getSnapshot().contentVersion;
  const mediaLabels = generation ? reviewParameterLabels({ ...generation.parameters, layout: generation.source?.scene === 'shot_image' ? generation.source.layout : undefined }) : [];
  if (!schemaReady) return null;
  return <>
    <section className="agent-artifact-shelf" aria-label="共享创作候选">
      <button className="agent-artifact-toggle" type="button" aria-expanded={open} aria-controls="agent-artifact-list" onClick={() => setOpen(value => !value)}><strong>创作候选{total > 0 ? `（${total}）` : ''}</strong><span>项目共享 · {open ? '收起' : '展开'}</span></button>
      {open && <div id="agent-artifact-list"><div className="agent-artifact-filters"><Select aria-label="候选类型" value={kind} allowClear placeholder="全部类型" disabled={busy} options={Object.entries(artifactKindLabels).map(([value, label]) => ({ value, label }))} onChange={value => { setKind(value); setOffset(0); }}/><Select aria-label="候选状态" value={status} allowClear placeholder="全部状态" disabled={busy} options={Object.entries(artifactStatusLabels).map(([value, label]) => ({ value, label }))} onChange={value => { setStatus(value); setOffset(0); }}/><Button loading={loading} disabled={busy} onClick={() => refresh(true)}>刷新候选</Button></div>
        <p className="agent-artifact-help">项目成员可查看和采用候选。采用作品不会自动继续任何对话。</p>
        {error && <Alert type="error" message={error}/>} {loading && !items.length ? <Skeleton active paragraph={{ rows: 2 }}/> : items.length ? <div className="agent-artifact-rows">{items.map(item => <article key={item.id} data-artifact-id={item.id}><div><h3>{artifactKindLabels[item.kind]} <span className={`artifact-status is-${item.status}`}>{artifactStatusLabels[item.status]}</span></h3><p>{item.preview || artifactTarget(item)}</p><span>{item.source_snapshot.model_name || '创作模型'} · {new Date(item.created_at).toLocaleString('zh-CN')}</span></div><Button onClick={() => show(item.id)}>核对候选</Button></article>)}</div> : !error && <p className="agent-artifact-help">生成的候选会保留在这里，核对后再采用。</p>}
        {items.length < total && <Button loading={loading} onClick={() => setOffset(items.length)}>更多候选（{items.length}/{total}）</Button>}
      </div>}
    </section>
    {detailId && <Dialog title="核对创作候选" className="agent-artifact-dialog" canClose={!busy} onClose={close}><div className="agent-artifact-detail">
      {detailError && <Alert type="error" showIcon message={detailError}/>} {message && <Alert type={detail?.status === 'applied' ? 'success' : 'info'} showIcon message={message}/>}
      {detailLoading && <Skeleton active paragraph={{ rows: 4 }}/>} {detail && <>
        <div className="agent-artifact-detail-heading"><h3>{artifactKindLabels[detail.kind]}</h3><span className={`artifact-status is-${detail.status}`}>{artifactStatusLabels[detail.status]}</span></div>
        <p>{artifactEffect(detail.kind)}</p><p className="agent-artifact-help">{artifactTarget(detail)} · {detail.source_snapshot.model_name || '创作模型'} · {new Date(detail.created_at).toLocaleString('zh-CN')}</p>
        <details className="agent-artifact-source"><summary>生成来源快照</summary><p>正文版本 {detail.source_snapshot.content_version} · 分镜版本 {detail.source_snapshot.storyboard_version}{detail.source_snapshot.target_row_version ? ` · 对象版本 ${detail.source_snapshot.target_row_version}` : ''}</p></details>
        {textStale && <Alert type="warning" message="正文已变化，这份候选基于旧版本。请保留候选并重新生成，当前作品不会被覆盖。"/>}
        {detail.content && <pre className="agent-artifact-text">{detail.content}</pre>}
        {!!detail.diff.length && <div className="agent-artifact-diff" role="region" aria-label="候选修改比较">{detail.diff.filter(item => artifactFieldLabels[item.field]).map(item => <section key={item.field}><h4>{artifactFieldLabels[item.field]}</h4><div><p><span>当前来源</span>{diffValue(item.field, item.before)}</p><p><span>建议修改</span>{diffValue(item.field, item.after)}</p></div></section>)}</div>}
        {detail.kind === 'storyboard_candidate' && detail.generation_task_id && <StoryboardResultPreview projectId={projectId} episodeId={episodeId} generationId={detail.generation_task_id} busy={busy} disabled={readOnly || detail.status !== 'ready'} onApply={mode => void adoptStoryboard(mode)}/>}
        {media && <div className="agent-artifact-media">{media.url ? media.media_type === 'video' ? <video controls preload="metadata" src={media.url} aria-label="候选视频预览"/> : <PreviewImage src={media.url} alt="候选图片"/> : <p>预览链接不可用，请重新核对候选。</p>}{!!mediaLabels.length && <p>{mediaLabels.join(' · ')}</p>}</div>}
        {detail.kind === 'extraction_candidate' && <p>将打开现有素材审核，逐项选择新建或复用。审核完成后，回到指定对话明确继续。</p>}
        {canResume && <p className="agent-artifact-help">继续当前对话可能产生模型费用；其他对话不会自动继续。</p>}
      </>}
    </div><div className="dialog-actions agent-artifact-actions"><Button disabled={busy} onClick={close}>关闭</Button><Button disabled={busy || detailLoading} onClick={() => void loadDetail(detailId)}>核对采用状态</Button>
      {detail?.status === 'ready' && !nativeArtifact(detail.kind) && <><Button type="primary" loading={busy} disabled={readOnly || detailLoading || !!textStale} onClick={() => void adopt()}>确认采用</Button>{canResume && <Button loading={busy} disabled={readOnly || detailLoading || !!textStale} onClick={() => void adopt(true)}>采用并继续</Button>}</>}
      {detail?.status === 'ready' && (detail.kind === 'image_candidate' || detail.kind === 'video_candidate') && <><Button type="primary" loading={busy} disabled={readOnly || detailLoading || !media?.url || !generation} onClick={() => void adoptMedia()}>确认采用媒体</Button>{canResume && <Button loading={busy} disabled={readOnly || detailLoading || !media?.url || !generation} onClick={() => void adoptMedia(true)}>采用并继续</Button>}</>}
      {detail?.kind === 'extraction_candidate' && detail.generation_task_id && <Button type="primary" disabled={busy} onClick={() => { const artifact = detail; close(); onOpenExtraction(artifact); }}>打开素材审核</Button>}
      {detail?.status === 'applied' && canResume && <Button type="primary" loading={busy} disabled={readOnly || detailLoading} onClick={() => void runAction(() => continued(detail))}>继续当前对话</Button>}
    </div></Dialog>}
  </>;
}
