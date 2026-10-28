import { confirmAction } from '../../../components/ui/confirm';
import { useCallback, useEffect, useRef, useState } from 'react';
import { SoundPanel } from '../../../features/projects/SoundPanel';
import { Button, Checkbox, Dropdown, Progress, Select, Spin, Tooltip } from 'antd';
import { Download, Ellipsis, History, Maximize2, Minimize2, Play, RefreshCw, Upload, Video, X } from 'lucide-react';
import { AssemblyAction } from '../../../features/projects/AssemblyAction';
import { StudioFeedback } from '../../../components/ui/StudioFeedback';
import { Dialog } from '../../../components/ui/Dialog';
import { Icon } from '../../../components/ui/Icon';
import { renderBody, type RenderJob } from '../../../api/modules/assembly';
import { creationScope, CreationRecoveryError } from '../../../features/projects/creation-recovery';
import { beginRenderAttempt, definiteRenderRejection, discardDamagedRenderAttempt, finishRenderAttempt, readRenderAttempt, readRenderRecoveryRecord, type RenderAttempt } from '../../../features/projects/render-recovery';
import { ApiError, errorMessage } from '../../../api/http';
import { clipFrames, FPS, isIncludedVideo, timecode } from '../../../features/projects/assembly-editing';
import { assemblyOutputAspect } from '../../../features/projects/assembly-document';
import { AssemblyWorkbench } from '../../../features/projects/AssemblyWorkbench';
import { useAssembly } from '../../../features/projects/useAssembly';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import '../../../app/assembly.css';
import '../../../features/projects/standard-assembly.css';

const stages: Record<string, string> = { queued: '等待合成', preparing: '准备视频', rendering: '合成片段', joining: '拼接成片', uploading: '保存成片', complete: '已完成', failed: '导出失败', cancelled: '已取消' };

export function AssemblyStage({ projectId, readOnly, registerBarrier, onStoryboard }: {
  projectId: string; readOnly: boolean; registerBarrier: (barrier: NavigationBarrier | null) => void; onStoryboard: () => void;
}) {
  const barriers = useRef<{ video: NavigationBarrier | null; sound: NavigationBarrier | null }>({ video: null, sound: null });
  const registerVideo = useCallback((value: NavigationBarrier | null) => { barriers.current.video = value; }, []);
  const registerSound = useCallback((value: NavigationBarrier | null) => { barriers.current.sound = value; }, []);
  useEffect(() => {
    registerBarrier({ hasUnsettled: () => Object.values(barriers.current).some(b => b?.hasUnsettled()), flush: async () => {
      for (const barrier of Object.values(barriers.current)) if (barrier?.hasUnsettled() && !await barrier.flush()) return false;
      return true;
    } });
    return () => registerBarrier(null);
  }, [registerBarrier]);
  const editor = useAssembly(projectId, registerVideo);
  const { value, api } = editor;
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [preparationError, setPreparationError] = useState('');
  const [syncOpen, setSyncOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [history, setHistory] = useState<RenderJob[] | null>(null);
  const [moreHistory, setMoreHistory] = useState(false);
  const [result, setResult] = useState<RenderJob | null>(null);
  const [applying, setApplying] = useState<RenderJob | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewJob, setPreviewJob] = useState<string | null>(null);
  const [previewStatus, setPreviewStatus] = useState<RenderJob | null>(null);
  const [previewError, setPreviewError] = useState('');
  const mutation = useRef(false);
  const completedPreviews = useRef(new Set<string>());
  const [focused, setFocused] = useState(false);
  const [soundOpen, setSoundOpen] = useState(false);
  const [soundTarget, setSoundTarget] = useState<HTMLDivElement | null>(null);
  const workspace = useRef<HTMLDivElement>(null);
  function setFocusMode(next: boolean) {
    setFocused(next);
    requestAnimationFrame(() => {
      workspace.current?.scrollIntoView({ block: 'start' });
      workspace.current?.querySelector<HTMLButtonElement>('.assembly-focus-toggle')?.focus({ preventScroll: true });
    });
  }
  const renderScope = useRef(creationScope(`assembly:${projectId}:render`)).current;
  const [pendingRender, setPendingRender] = useState<RenderAttempt | null>(null);
  const [damagedRender, setDamagedRender] = useState<string | null>(null);
  const [renderRecoveryError, setRenderRecoveryError] = useState('');
  const loadRenderRecovery = useCallback(() => {
    try {
      const record = readRenderRecoveryRecord(renderScope);
      setPendingRender(record.attempt); setDamagedRender(record.damaged);
      setRenderRecoveryError(record.damaged !== null ? '本机合成请求记录损坏，已阻止新请求，完整原文仍保留。' : '');
    } catch (cause) {
      setPendingRender(null); setDamagedRender(null);
      setRenderRecoveryError(cause instanceof Error ? cause.message : '无法读取本机合成请求恢复记录。');
    }
  }, [renderScope]);
  useEffect(loadRenderRecovery, [loadRenderRecovery]);
  async function submitRender(operation: Omit<RenderAttempt, 'format' | 'key'>) {
    const attempt = beginRenderAttempt(renderScope, operation);
    setPendingRender(attempt);
    let job: RenderJob;
    try { job = await api.submitRender(attempt.kind, attempt.body, attempt.key, attempt.job_id); }
    catch (cause) {
      if (cause instanceof ApiError && definiteRenderRejection(cause.code, cause.status)) {
        finishRenderAttempt(renderScope, attempt); setPendingRender(null);
      }
      throw cause;
    }
    finishRenderAttempt(renderScope, attempt); setPendingRender(null);
    return job;
  }
  async function reconcileRender() {
    const attempt = readRenderAttempt(renderScope);
    if (!attempt) { setPendingRender(null); return; }
    const job = await submitRender(attempt);
    if (attempt.kind === 'preview') {
      if (job.status === 'succeeded') setResult(job);
      else if (['queued', 'running'].includes(job.status)) { setPreviewStatus(job); setPreviewJob(job.id); }
    }
    await editor.load();
  }
  const clips = value?.clips ?? [];
  const included = clips.filter(isIncludedVideo);
  const total = included.reduce((sum, c) => sum + clipFrames(c), 0) / FPS * 1000;
  const blocked = included.filter(c => c.issue);
  const stale = included.some(c => c.is_stale);
  const activeExports = value?.jobs?.filter(j => j.kind === 'export' && ['queued', 'running'].includes(j.status)) ?? [];
  const active = activeExports[0];
  const latestExport = value?.jobs?.find(j => j.kind === 'export');
  const preparation = value?.jobs?.find(j => j.kind === 'probe' && j.status === 'failed');
  const current = value?.jobs?.find(j => j.media_id && j.media_id === value?.assembly?.current_media_id);
  const displayedHistory = history?.map(job => value?.jobs?.find(latest => latest.id === job.id) ?? job);
  const disabled = readOnly || busy || previewBusy || !!renderRecoveryError;
  const preparedAssembly = useRef<string | null>(null);
  const needsThumbnails = (value?.sources ?? []).some(c => c.url && !c.filmstrip && c.issue !== 'invalid');
  useEffect(() => {
    const id = value?.assembly?.id;
    if (!id || readOnly || !needsThumbnails || preparedAssembly.current === id) return;
    preparedAssembly.current = id;
    // initialize is idempotent for an existing draft: it prepares derivatives,
    // never syncs sources or changes edit decisions. load preserves pending edits.
    void api.initialize().then(() => { setPreparationError(''); return editor.load(); }).catch(() => setPreparationError('缩略图准备失败，可通过“同步分镜视频”重试。'));
  }, [value?.assembly?.id, readOnly, needsThumbnails, api]);

  async function action(work: () => Promise<void>) {
    if (mutation.current) return;
    mutation.current = true;
    setBusy(true); setNotice(''); setPreparationError('');
    try { await work(); } catch (cause) { if (cause instanceof CreationRecoveryError) loadRenderRecovery(); setNotice(cause instanceof CreationRecoveryError ? cause.message : errorMessage(cause)); } finally { mutation.current = false; setBusy(false); }
  }
  async function flushRenderDraft() {
    if (!await editor.flush()) return false;
    const sound = barriers.current.sound;
    if (sound?.hasUnsettled() && !await sound.flush()) {
      setNotice('声音或字幕尚未保存。请先在声音面板处理保存问题，再合成。');
      return false;
    }
    await editor.load();
    return true;
  }
  async function preview() {
    if (mutation.current || previewJob) return;
    mutation.current = true;
    setPreviewBusy(true); setNotice('');
    try {
      if (!await flushRenderDraft()) return;
      const latest = editor.latest()!;
      if (latest.clips?.some(c => isIncludedVideo(c) && c.is_stale) && !await confirmAction('部分片段使用此前采用的分镜视频，是否保留当前轨道并合成预览？', { className: 'standard-creation-dialog' })) return;
      const job = await submitRender({ kind: 'preview', body: renderBody(latest, true) });
      setPreviewError(''); setPreviewStatus(job);
      if (job.status === 'succeeded') { completedPreviews.current.add(job.id); setResult(job); }
      else if (['failed', 'cancelled'].includes(job.status)) setNotice(job.error?.message ?? '合成预览已取消，可以重新生成。');
      else setPreviewJob(job.id);
      await editor.load();
    } catch (cause) { if (cause instanceof CreationRecoveryError) loadRenderRecovery(); setNotice(cause instanceof CreationRecoveryError ? cause.message : errorMessage(cause)); }
    finally { mutation.current = false; setPreviewBusy(false); }
  }
  const restoredPreview = value?.jobs?.find(j => j.kind === 'preview' && ['queued', 'running'].includes(j.status) && !j.cancel_requested)?.id;
  useEffect(() => {
    if (restoredPreview && !previewJob && !completedPreviews.current.has(restoredPreview)) setPreviewJob(restoredPreview);
  }, [restoredPreview, previewJob]);
  useEffect(() => {
    if (!previewJob) return;
    let disposed = false;
    let finished = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const job = await api.job(previewJob, controller.signal);
        if (disposed) return;
        setPreviewStatus(job); setPreviewError('');
        if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
          finished = true; completedPreviews.current.add(job.id);
          if (job.status === 'succeeded') setResult(job);
          else setNotice(job.error?.message ?? '合成预览已取消，可以重新生成。');
          setPreviewJob(null);
          void editor.load().catch(() => {});
        }
      } catch (cause) {
        if (!disposed) setPreviewError(`${errorMessage(cause)} 后台任务仍在运行，正在自动重新连接。`);
      } finally {
        if (!disposed && !finished) timer = setTimeout(() => void poll(), 2000);
      }
    };
    void poll();
    return () => { disposed = true; controller.abort(); clearTimeout(timer); };
  }, [previewJob, api]);
  async function prepareExport() {
    if (!await flushRenderDraft()) return;
    setAcknowledge(false); setExportOpen(true);
  }
  async function submitExport() {
    if (!await flushRenderDraft()) return;
    const latest = editor.latest()!;
    await submitRender({ kind: 'export', body: renderBody(latest, acknowledge) });
    setExportOpen(false); await editor.load();
  }
  async function openHistory() {
    const page = await api.history(); setHistory(page.items); setMoreHistory(page.has_more);
  }
  async function retry(job: RenderJob) {
    await submitRender({ kind: 'retry', body: {}, job_id: job.id });
    await editor.load(); if (history) await openHistory();
  }
  function backup() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(editor.latest(), null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `assembly-${projectId}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function backupRecovery() {
    try {
      const content = editor.exportRecovered();
      if (!content) return;
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `assembly-${projectId}-recovery.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : '无法下载剪辑恢复记录。'); }
  }
  function backupRenderRecovery() {
    if (damagedRender === null) return;
    try {
      const url = URL.createObjectURL(new Blob([damagedRender], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `assembly-${projectId}-render-recovery.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : '无法下载合成请求恢复记录。'); }
  }
  async function discardRenderRecovery() {
    const raw = damagedRender;
    if (raw === null || !await confirmAction('请先下载原请求记录，并检查当前作品的导出和预览记录。清除后将失去原请求编号的核对依据；若后台已受理，再发起合成可能重复制作。确认已核对并清除这条损坏的本机记录？', { title: '清除损坏的合成请求记录', confirmText: '已核对，清除记录', danger: true, className: 'standard-creation-dialog' })) return;
    discardDamagedRenderAttempt(renderScope, raw);
    loadRenderRecovery();
  }

  const feedbackTarget = applying ? 'apply' : history ? 'history' : exportOpen ? 'export' : syncOpen ? 'sync' : 'main';
  const workspaceFeedback = <>
    {notice && <StudioFeedback type="error" noticeKey={`assembly:${projectId}:action`} message={notice}/>}
    {preparationError && <StudioFeedback type="warning" noticeKey={`assembly:${projectId}:preparation-error`} quiet message={preparationError} actions={<Button disabled={disabled} onClick={() => void action(async () => { if (await editor.flush()) { await editor.load(); setSyncOpen(true); } })}>同步分镜视频</Button>}/>}
    {renderRecoveryError && <StudioFeedback type="warning" noticeKey={`assembly:${projectId}:render-recovery`} message="合成请求恢复记录无法读取" description={<>
      <p>{renderRecoveryError} 请先下载原请求记录，并检查当前作品的导出和预览记录。清除后将失去原请求编号的核对依据；若后台已受理，再发起合成可能重复制作。</p>
      <div className="assembly-inline-actions"><Button disabled={busy || previewBusy || damagedRender === null} onClick={backupRenderRecovery}>下载完整合成请求记录</Button>{damagedRender !== null && <Button danger disabled={busy || previewBusy} onClick={() => void action(discardRenderRecovery)}>清除损坏的本机记录</Button>}<Button disabled={busy || previewBusy} onClick={loadRenderRecovery}>重新读取恢复记录</Button></div>
    </>}/>}
    {pendingRender && <StudioFeedback type="warning" noticeKey={`assembly:${projectId}:pending-render`} message="上次合成请求尚未核对" description="将使用原参数和原请求编号核对受理结果，避免重复制作。当前编辑仍保留。" actions={<Button loading={busy} disabled={disabled} onClick={() => void action(reconcileRender)}>核对原合成请求</Button>}/>}
    {editor.connectionError && <StudioFeedback type="warning" noticeKey={`assembly:${projectId}:connection`} message="成片进度暂时无法同步" description={`${editor.connectionError} 当前显示上次同步的进度，编辑草稿仍保留。${editor.lastSyncedAt ? `最近同步：${new Date(editor.lastSyncedAt).toLocaleTimeString('zh-CN')}` : ''}`} actions={<Button loading={busy} disabled={disabled} onClick={() => void action(async () => { await editor.load(); })}>重新连接</Button>}/>}
    {editor.recoveryError && <StudioFeedback type="warning" noticeKey={`assembly:${projectId}:draft-recovery`} message={editor.recoveryError} actions={<div className="assembly-inline-actions"><Button onClick={editor.recoveryBlocked ? backupRecovery : backup}>{editor.recoveryBlocked ? '下载完整恢复稿' : '下载草稿'}</Button>{editor.recoveryBlocked && <Button disabled={busy} onClick={() => void action(async () => { await editor.discardRecovered(); })}>使用服务端版本</Button>}</div>}/>}
    {editor.recoveredDraft && <StudioFeedback type="warning" noticeKey={`assembly:${projectId}:draft-conflict`} message="剪辑存在不同版本，本机输入已保留" description={`恢复稿基准版本 ${editor.recoveredDraft.base_version}，当前服务端版本 ${value?.assembly?.row_version}。恢复将重新读取最新版本，并核对片段媒体；无效来源仍需处理。`} actions={<div className="assembly-inline-actions"><Button disabled={readOnly || busy || previewBusy} onClick={() => void action(async () => { await editor.restoreRecovered(); })}>以当前版本保留本机修改</Button><Button disabled={busy} onClick={() => void action(async () => { await editor.discardRecovered(); })}>使用服务端版本</Button></div>}/>}
    {editor.error && !editor.recoveryError && !editor.recoveredDraft && <StudioFeedback type="error" noticeKey={`assembly:${projectId}:editor-error`} message={editor.error} description={value ? '当前输入已保留，可继续切换；版本不同请核对后选择。' : '成片工作台未能载入，请检查连接后重新加载。'} actions={<div className="assembly-inline-actions">{value && <Button onClick={backup}>下载草稿</Button>}{editor.status === 'conflict' ? <Button disabled={disabled} onClick={() => void action(async () => { await editor.restoreRecovered(); })}>以当前版本保留本机修改</Button> : <Button disabled={disabled} onClick={() => void action(async () => { await editor.retrySave(); })}>{value ? '重试保存' : '重新加载'}</Button>}{value && <Button disabled={disabled} onClick={() => void action(async () => { await editor.reload(); })}>使用服务端版本</Button>}</div>}/>}
  </>;

  return <div ref={workspace} className={`assembly-workspace${focused ? ' is-focused' : ''}`} onKeyDown={event => {
    if (event.key === 'Escape' && focused && !event.defaultPrevented && !document.fullscreenElement
      && !document.querySelector('dialog[open], [role="dialog"], [role="combobox"][aria-expanded="true"]')
      && !(event.target as HTMLElement).closest('input, textarea, select')) {
      event.preventDefault(); setFocusMode(false);
    }
  }}>
    <header className="assembly-heading"><div className="assembly-heading-title"><Video size={17}/><h2>成片导出</h2>{value?.assembly && <span className="assembly-creation-meta">{value.assembly.aspect === 'auto' ? '自动' : value.assembly.aspect}<span aria-hidden="true">/</span>{included.length} 段<span aria-hidden="true">/</span>{timecode(total)}</span>}</div>
      {value?.assembly && <div className="assembly-heading-actions" role="group" aria-label="成片设置与同步">
        <SoundPanel key={`${projectId}`} projectId={projectId} disabled={disabled} readOnly={readOnly} flushVideo={editor.flush} onSaved={editor.load} registerBarrier={registerSound} panelContainer={soundTarget} onOpenChange={setSoundOpen}/>
        <AssemblyAction label="同步分镜视频" icon={<RefreshCw size={16}/>} disabled={disabled} onClick={() => void action(async () => { if (await editor.flush()) { await editor.load(); setSyncOpen(true); } })}/>
        <AssemblyAction label="导出记录" icon={<History size={16}/>} aria-busy={busy} loading={busy} onClick={() => void action(openHistory)}/>
        {latestExport?.status === 'succeeded' && <div className="assembly-latest-export" role="status" aria-label="最近导出已完成"><Button size="small" type="text" title={`导出完成 · ${new Date(latestExport.created_at).toLocaleString()} · ${latestExport.media_id === current?.media_id ? '当前已采用' : '尚未采用'}`} onClick={() => setResult(latestExport)}>播放最新导出</Button><AssemblyAction label="下载最新导出 MP4" icon={<Download size={16}/>} href={api.download(latestExport.id)}/></div>}
        <AssemblyAction className="assembly-focus-toggle" label={focused ? '退出专注剪辑' : '专注剪辑'} icon={focused ? <Minimize2 size={16}/> : <Maximize2 size={16}/>} aria-pressed={focused} onClick={() => setFocusMode(!focused)}/>
        {editor.hasPrevious() && <Dropdown trigger={['click']} overlayClassName="standard-creation-popup" menu={{ items: [{ key: 'backup', label: '下载本机备份', icon: <Download size={15}/>, onClick: backupRecovery }] }}><Button type="text" className="assembly-icon-action" icon={<Ellipsis size={16}/>} aria-label="更多成片操作" title="更多成片操作"/></Dropdown>}
        <Tooltip title={blocked.length ? `${blocked.length} 个片段需要处理` : !included.length ? '请先添加分镜视频' : active ? `导出进度 ${active.progress}%` : '导出成片'} trigger={['hover', 'focus']}><span className="assembly-export-entry" tabIndex={0}><Button type="primary" aria-label="导出成片" icon={<Upload size={16}/>} loading={busy || !!active} disabled={disabled || !included.length || !!blocked.length || !!active || editor.status === 'error'} onClick={() => void action(prepareExport)}>{active ? `${active.progress}%` : null}</Button></span></Tooltip>
      </div>}
    </header>
    {feedbackTarget === 'main' && workspaceFeedback}
    {!value && editor.status === 'loading' && <div className="assembly-empty"><Spin/><p>正在载入成片工作台…</p></div>}
    {value && !value.assembly && <div className="assembly-empty"><div className="assembly-empty-frame"><Icon name="film" size={32}/></div><h3>还没有成片草稿</h3><p>{value.source_count ? '使用本集分镜视频开始剪辑' : '创建草稿后添加视频或图片'}</p><Button type="primary" disabled={disabled} loading={busy} onClick={() => void action(async () => { editor.replace(await api.initialize()); })}>创建成片草稿</Button><Button type="link" onClick={onStoryboard}>返回分镜制作</Button></div>}
    {value?.assembly && <>
      {preparation && (needsThumbnails || clips.some(c => c.issue === 'preparing')) && <StudioFeedback type="warning" noticeKey={`assembly:${projectId}:preparation`} quiet message="视频预览素材准备未完成，可重试；已就绪的视频仍可剪辑。" actions={<Button disabled={disabled || preparation.can_retry === false} onClick={() => void action(() => retry(preparation))}>重试检测</Button>}/>}
      <div className={`assembly-stage-body${soundOpen ? ' is-sound-open' : ''}`}>
        <AssemblyWorkbench editor={editor} disabled={disabled} soundOpen={soundOpen} result={result} onResult={setResult} onPreview={() => void preview()} previewBusy={previewBusy || !!previewJob} previewProgress={previewStatus?.progress} focused={focused}/>
        <div ref={setSoundTarget} className="assembly-sound-target" hidden={!soundOpen}/>
      </div>
      {previewJob && <StudioFeedback type={previewError ? 'warning' : 'info'} noticeKey={`assembly:${projectId}:preview:${previewJob}`} quiet persistent message={previewStatus?.cancel_requested ? '正在取消预览…' : '正在后台合成预览'} description={previewError || '可以继续编辑，预览使用提交时的版本。'} actions={<Button disabled={disabled || previewStatus?.can_cancel === false || previewStatus?.cancel_requested} onClick={() => void action(async () => { const cancelled = await api.cancel(previewJob); setPreviewStatus(cancelled); await editor.load(); })}>取消预览</Button>}>{previewStatus && <Progress percent={previewStatus.progress} size="small"/>}</StudioFeedback>}
      {current && <StudioFeedback type="info" noticeKey={`assembly:${projectId}:current`} quiet persistent message={`当前成片${current.is_stale ? ' · 草稿已有新修改' : ''}`} actions={<div className="assembly-inline-actions"><Button type="link" onClick={() => setResult(current)}>播放成片</Button><a href={api.download(current.id)}>下载 MP4</a></div>}/>}
      {activeExports.map(job => <StudioFeedback type="info" noticeKey={`assembly:${projectId}:export:${job.id}`} quiet persistent key={job.id} message={job.cancel_requested ? '正在取消合成…' : stages[job.stage] ?? '正在合成'} description="可以继续编辑，当前导出使用提交时的版本。" actions={<Button disabled={disabled || job.can_cancel === false || job.cancel_requested} onClick={() => void action(async () => { await api.cancel(job.id); await editor.load(); })}>取消导出</Button>}><Progress percent={job.progress} size="small"/></StudioFeedback>)}
      {latestExport && ['failed', 'cancelled'].includes(latestExport.status) && <StudioFeedback type={latestExport.status === 'failed' ? 'error' : 'info'} noticeKey={`assembly:${projectId}:latest-export:${latestExport.id}`} persistent message={latestExport.status === 'failed' ? '最近一次导出失败' : '最近一次导出已取消'} description={latestExport.error?.message ?? '编辑草稿已保留，可以重试这次导出，或调整后重新导出。'} actions={<div className="assembly-inline-actions"><Button disabled={disabled || latestExport.can_retry === false || !!active} onClick={() => void action(() => retry(latestExport))}>重试这次导出</Button><Button disabled={disabled} onClick={() => void action(openHistory)}>查看导出记录</Button></div>}/>}
      {latestExport?.status === 'succeeded' && latestExport.media_id !== current?.media_id && <StudioFeedback type="success" noticeKey={`assembly:${projectId}:latest-export:${latestExport.id}`} quiet persistent message={`最近一次导出已完成${latestExport.is_stale ? ' · 草稿已有修改' : ''}`} actions={<div className="assembly-inline-actions"><Button onClick={() => setResult(latestExport)}>播放导出结果</Button><a href={api.download(latestExport.id)}>下载导出 MP4</a><Button disabled={disabled} onClick={() => void action(async () => { if (!await flushRenderDraft()) return; setApplying({ ...latestExport, is_stale: editor.latest()?.context_hash !== latestExport.context_hash }); })}>设为当前成片</Button></div>}/>}
    </>}
    {syncOpen && <Dialog title="同步分镜视频" className="standard-creation-dialog assembly-operation-dialog" canClose={!busy} onClose={() => setSyncOpen(false)}>
      {feedbackTarget === 'sync' && workspaceFeedback}
      <div className="assembly-dialog-body"><p>保留现有顺序与裁剪。替换视频会更新该镜头的所有片段，视频变短时需重新裁剪。</p>{value?.changes?.length ? <div className="assembly-sync-list">{value.changes.map(c => <div key={c.shot_id}><strong>镜头 {String(c.position).padStart(2, '0')}</strong><span>{({ added: '追加到末尾', replacement: '替换来源视频', archived: '移出导出范围' })[c.kind]}</span></div>)}</div> : <p>没有视频变化，可重新检测未就绪的视频。</p>}</div>
      <div className="assembly-dialog-actions"><Button disabled={busy} onClick={() => setSyncOpen(false)}>取消</Button><Button type="primary" loading={busy} onClick={() => void action(async () => { if (await editor.flush()) { editor.replace(await api.sync(editor.latest()!)); editor.clearHistory(); setSyncOpen(false); } })}>确认同步</Button></div>
    </Dialog>}
    {exportOpen && <Dialog title="导出本集成片" className="standard-creation-dialog assembly-operation-dialog" canClose={!busy} onClose={() => setExportOpen(false)}>
      {feedbackTarget === 'export' && workspaceFeedback}
      <div className="assembly-dialog-body"><dl className="assembly-export-summary"><div><dt>片段</dt><dd>{included.length} 段</dd></div><div><dt>时长</dt><dd>{timecode(total)}</dd></div><div><dt>画幅</dt><dd>{value?.assembly?.aspect === 'auto' ? `自动 · ${assemblyOutputAspect(value)}` : value?.assembly?.aspect}</dd></div><div><dt>格式</dt><dd>MP4</dd></div></dl><label className="assembly-export-resolution">输出画幅<Select classNames={{ popup: { root: 'standard-creation-popup' } }} aria-label="输出画幅" value={value?.assembly?.aspect ?? 'auto'} disabled={disabled} options={[{ value: 'auto', label: '自动（跟随视频）' }, { value: '16:9', label: '16:9' }, { value: '9:16', label: '9:16' }]} onChange={aspect => editor.edit(state => ({ ...state, assembly: { ...state.assembly!, aspect } }))}/></label><label className="assembly-export-resolution">导出清晰度<Select classNames={{ popup: { root: 'standard-creation-popup' } }} aria-label="导出清晰度" value={value?.assembly?.resolution} disabled={disabled} options={[{ value: '720p', label: '720p' }, { value: '1080p', label: '1080p' }]} onChange={resolution => editor.edit(state => ({ ...state, assembly: { ...state.assembly!, resolution } }))}/></label><p className="assembly-export-hint">{value?.assembly?.output_width && value.assembly.output_height ? `实际输出 ${value.assembly.output_width} × ${value.assembly.output_height}。` : '视频尺寸检测中。'}自动优先使用时间轴中的首个分镜视频，其次使用导入视频。声音和字幕使用已保存的编辑设置，导出期间可以继续剪辑。</p>{stale && <Checkbox checked={acknowledge} onChange={e => setAcknowledge(e.target.checked)}>已核对，继续使用轨道中的旧版分镜视频</Checkbox>}</div>
      <div className="assembly-dialog-actions"><Button onClick={() => setExportOpen(false)} disabled={busy}>返回编辑</Button><Button type="primary" icon={<Upload size={16}/>} loading={busy} disabled={disabled || !!blocked.length || (stale && !acknowledge)} onClick={() => void action(submitExport)}>开始导出</Button></div>
    </Dialog>}
    {history && <Dialog title="导出记录" className="standard-creation-dialog assembly-operation-dialog assembly-history-dialog" onClose={() => setHistory(null)}>
      {feedbackTarget === 'history' && workspaceFeedback}
      <div className="assembly-dialog-body">{!history.length && <p>还没有导出记录</p>}<div className="assembly-history">{displayedHistory?.map(job => <article key={job.id}>
        <div className="assembly-history-meta"><strong>{stages[job.stage] ?? ({ succeeded: '已完成', queued: '排队中', running: '导出中', failed: '导出失败', cancelled: '已取消' })[job.status]}</strong><time>{new Date(job.created_at.endsWith('Z') ? job.created_at : `${job.created_at}Z`).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</time><span>{job.resolution ? `${job.resolution} / ` : ''}{job.duration_ms ? `${timecode(job.duration_ms)} / ` : ''}{job.media_id === value?.assembly?.current_media_id ? '当前成片' : job.is_stale ? '与当前草稿不同' : '当前草稿版本'}</span>{job.error && <details><summary>失败原因</summary><p role="alert">{job.error.message}</p></details>}</div>
        <div className="assembly-inline-actions">{job.status === 'succeeded' && <><AssemblyAction label="播放" icon={<Play size={16}/>} onClick={() => { setResult(job); setHistory(null); }}/><AssemblyAction label="下载" icon={<Download size={16}/>} href={api.download(job.id)}/><Dropdown trigger={['click']} overlayClassName="standard-creation-popup" menu={{ items: [{ key: 'apply', label: '设为当前成片', disabled: disabled || job.media_id === value?.assembly?.current_media_id, onClick: () => void action(async () => { if (!await flushRenderDraft()) return; setApplying({ ...job, is_stale: editor.latest()?.context_hash !== job.context_hash }); }) }] }}><Button type="text" className="assembly-icon-action" icon={<Ellipsis size={16}/>} aria-label="更多导出操作" title="更多导出操作"/></Dropdown></>}
          {['failed', 'cancelled'].includes(job.status) && <AssemblyAction label="重试" icon={<RefreshCw size={16}/>} disabled={disabled || job.can_retry === false || !!active} onClick={() => void action(() => retry(job))}/>}
          {['queued', 'running'].includes(job.status) && <><span>{job.progress}%</span><AssemblyAction label="取消" icon={<X size={16}/>} disabled={disabled || job.can_cancel === false || job.cancel_requested} onClick={() => void action(async () => { await api.cancel(job.id); await openHistory(); await editor.load(); })}/></>}
        </div></article>)}</div></div>
      <div className="assembly-dialog-actions"><AssemblyAction label="刷新记录" icon={<RefreshCw size={16}/>} disabled={busy} onClick={() => void action(openHistory)}/>{moreHistory && <Button disabled={busy} onClick={() => void action(async () => { const page = await api.history(history.length); setHistory([...history, ...page.items]); setMoreHistory(page.has_more); })}>加载更早记录</Button>}</div>
    </Dialog>}
    {applying && <Dialog title="设为当前成片" className="standard-creation-dialog assembly-operation-dialog" canClose={!busy} onClose={() => setApplying(null)}>
      {feedbackTarget === 'apply' && workspaceFeedback}
      <div className="assembly-dialog-body"><p>将采用这次导出的结果作为本集当前成片。其他导出记录仍会保留。</p>{applying.is_stale && <p>此结果与当前草稿不同，请确认画面和顺序。</p>}</div>
      <div className="assembly-dialog-actions"><Button disabled={busy} onClick={() => setApplying(null)}>取消</Button><Button type="primary" loading={busy} onClick={() => void action(async () => { if (!await editor.flush()) return; await api.apply(applying.id, editor.latest()!, applying.is_stale); setApplying(null); await editor.load(); if (history) await openHistory(); })}>确认采用</Button></div>
    </Dialog>}
  </div>;
}
