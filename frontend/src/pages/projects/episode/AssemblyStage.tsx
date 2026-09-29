import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Progress, Select, Spin } from 'antd';
import { Dialog } from '../../../components/ui/Dialog';
import { Icon } from '../../../components/ui/Icon';
import type { RenderJob } from '../../../api/modules/assembly';
import { errorMessage } from '../../../api/http';
import { clipFrames, FPS, timecode } from '../../../features/projects/assembly-editing';
import { AssemblyWorkbench } from '../../../features/projects/AssemblyWorkbench';
import { useAssembly } from '../../../features/projects/useAssembly';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import '../../../app/assembly.css';

const stages: Record<string, string> = { queued: '等待合成', preparing: '准备视频', rendering: '合成片段', joining: '拼接成片', uploading: '保存成片', complete: '已完成', failed: '导出失败', cancelled: '已取消' };

export function AssemblyStage({ projectId, episodeId, readOnly, registerBarrier, onStoryboard }: {
  projectId: string; episodeId: string; readOnly: boolean; registerBarrier: (barrier: NavigationBarrier | null) => void; onStoryboard: () => void;
}) {
  const editor = useAssembly(projectId, episodeId, registerBarrier);
  const { value, api } = editor;
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [syncOpen, setSyncOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [history, setHistory] = useState<RenderJob[] | null>(null);
  const [moreHistory, setMoreHistory] = useState(false);
  const [result, setResult] = useState<RenderJob | null>(null);
  const [applying, setApplying] = useState<RenderJob | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewJob, setPreviewJob] = useState<string | null>(null);
  const [focused, setFocused] = useState(false);
  const workspace = useRef<HTMLDivElement>(null);
  function setFocusMode(next: boolean) {
    setFocused(next);
    requestAnimationFrame(() => {
      workspace.current?.scrollIntoView({ block: 'start' });
      workspace.current?.querySelector<HTMLButtonElement>('.assembly-focus-toggle')?.focus({ preventScroll: true });
    });
  }
  const previewKey = useRef<{ body: string; key: string } | null>(null);
  const request = useRef<{ body: string; key: string } | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  const clips = value?.clips ?? [];
  const included = clips.filter(c => c.included);
  const total = included.reduce((sum, c) => sum + clipFrames(c), 0) / FPS * 1000;
  const blocked = included.filter(c => c.issue);
  const stale = included.some(c => c.is_stale);
  const active = value?.jobs?.find(j => j.kind === 'export' && ['queued', 'running'].includes(j.status));
  const preparation = value?.jobs?.find(j => j.kind === 'probe' && j.status === 'failed');
  const current = value?.jobs?.find(j => j.media_id && j.media_id === value?.assembly?.current_media_id);
  const disabled = readOnly || busy;
  const preparedAssembly = useRef<string | null>(null);
  const needsThumbnails = (value?.sources ?? []).some(c => c.url && !c.filmstrip && c.issue !== 'invalid');
  useEffect(() => {
    const id = value?.assembly?.id;
    if (!id || readOnly || !needsThumbnails || preparedAssembly.current === id) return;
    preparedAssembly.current = id;
    // initialize is idempotent for an existing draft: it prepares derivatives,
    // never syncs sources or changes edit decisions. load preserves pending edits.
    void api.initialize().then(() => editor.load()).catch(() => setNotice('缩略图准备失败，可通过“同步分镜视频”重试。'));
  }, [value?.assembly?.id, readOnly, needsThumbnails, api]);

  async function action(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setNotice('');
    try { await work(); } catch (cause) { setNotice(errorMessage(cause)); } finally { setBusy(false); }
  }
  async function preview() {
    setPreviewBusy(true); setNotice('');
    try {
      if (!await editor.flush()) return;
      await editor.load();
      const latest = editor.latest()!;
      if (latest.clips?.some(c => c.included && c.is_stale) && !window.confirm('部分片段使用旧分镜视频，是否按当前轨道合成预览？')) return;
      const fingerprint = JSON.stringify([latest.assembly?.row_version, latest.source_hash]);
      if (previewKey.current?.body !== fingerprint) previewKey.current = { body: fingerprint, key: crypto.randomUUID() };
      const job = await api.preview(latest, true, previewKey.current.key);
      previewKey.current = null;
      if (job.status === 'succeeded') setResult(job);
      else setPreviewJob(job.id);
      await editor.load();
    } catch (cause) { setNotice(errorMessage(cause)); }
    finally { setPreviewBusy(false); }
  }
  const restoredPreview = value?.jobs?.find(j => j.kind === 'preview' && ['queued', 'running'].includes(j.status) && !j.cancel_requested)?.id;
  useEffect(() => { if (restoredPreview) setPreviewJob(restoredPreview); }, [restoredPreview]);
  useEffect(() => {
    if (!previewJob) return;
    let disposed = false;
    const poll = async () => {
      try {
        const job = await api.job(previewJob);
        if (disposed) return;
        if (job.status === 'succeeded') { setResult(job); setPreviewJob(null); }
        else if (job.status === 'failed' || job.status === 'cancelled') { setNotice(job.error?.message ?? '合成预览已取消，可以重新生成。'); setPreviewJob(null); }
      } catch (cause) { if (!disposed) { setNotice(errorMessage(cause)); setPreviewJob(null); } }
    };
    const timer = setInterval(() => void poll(), 2000);
    void poll();
    return () => { disposed = true; clearInterval(timer); };
  }, [previewJob, api]);
  async function prepareExport() {
    if (!await editor.flush()) return;
    await editor.load(); setAcknowledge(false); setExportOpen(true);
  }
  async function submitExport() {
    if (!await editor.flush()) return;
    const latest = editor.latest()!;
    const body = JSON.stringify([latest.assembly?.row_version, latest.source_hash, acknowledge]);
    if (request.current?.body !== body) request.current = { body, key: crypto.randomUUID() };
    await api.export(latest, acknowledge, request.current.key);
    request.current = null; setExportOpen(false); await editor.load();
  }
  async function openHistory() {
    const page = await api.history(); setHistory(page.items); setMoreHistory(page.has_more);
  }
  async function retry(job: RenderJob) {
    const key = retryKeys.current.get(job.id) ?? crypto.randomUUID(); retryKeys.current.set(job.id, key);
    await api.retry(job.id, key); retryKeys.current.delete(job.id); await editor.load(); if (history) await openHistory();
  }
  function backup() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(editor.latest(), null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `assembly-${episodeId}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div ref={workspace} className={`assembly-workspace${focused ? ' is-focused' : ''}`} onKeyDown={event => {
    if (event.key === 'Escape' && focused && !event.defaultPrevented && !document.fullscreenElement
      && !document.querySelector('dialog[open], [role="dialog"], [role="combobox"][aria-expanded="true"]')
      && !(event.target as HTMLElement).closest('input, textarea, select')) {
      event.preventDefault(); setFocusMode(false);
    }
  }}>
    <header className="assembly-heading"><div><h2>成片合成与导出</h2><p>把本集镜头串成完整故事。</p></div><div className="assembly-heading-actions"><span role="status" className={`assembly-save is-${editor.status}`}>{({ loading: '正在载入', saved: '成片草稿已保存', saving: '正在保存', unsaved: '等待保存', error: '保存已暂停' })[editor.status]}</span>{value?.assembly && <><Button className="assembly-focus-toggle" aria-pressed={focused} title={focused ? '退出专注剪辑（Esc）' : '收起创作流程，扩大剪辑空间'} onClick={() => setFocusMode(!focused)}>{focused ? '退出专注剪辑' : '专注剪辑'}</Button><Button onClick={() => void action(openHistory)}>导出记录</Button></>}</div></header>
    {notice && <Alert type="error" showIcon message={notice} closable onClose={() => setNotice('')}/>}
    {editor.error && <Alert type="error" showIcon message={editor.error} description="当前编辑保留在本页。版本冲突时，请先下载草稿，再载入服务端版本。" action={<div className="assembly-inline-actions"><Button onClick={backup}>下载草稿</Button><Button disabled={busy} onClick={() => void action(async () => { await editor.retrySave(); })}>重试保存</Button><Button disabled={busy} onClick={() => { if (window.confirm('载入将替换本页编辑，是否继续？')) void action(editor.reload); }}>重新载入</Button></div>}/>}
    {!value && editor.status === 'loading' && <div className="assembly-empty"><Spin/><p>正在载入成片工作台…</p></div>}
    {value && !value.assembly && <div className="assembly-empty"><div className="assembly-empty-frame"><Icon name="film" size={40}/></div><h3>让分镜成为一部作品</h3><p>从已采用的分镜视频开始，调整顺序、保留精彩片段，再导出本集成片。</p><Button type="primary" disabled={disabled || !value.source_count} loading={busy} onClick={() => void action(async () => { editor.replace(await api.initialize()); })}>创建成片草稿</Button><Button type="link" onClick={onStoryboard}>{value.source_count ? '返回分镜制作' : '先去创建分镜'}</Button></div>}
    {value?.assembly && <>
      <div className="assembly-context"><span>{value.assembly.aspect} 画幅 <span aria-hidden="true"> / </span> {clips.length} 个片段</span><Button disabled={disabled} onClick={() => void action(async () => { if (await editor.flush()) { await editor.load(); setSyncOpen(true); } })}>同步分镜视频{value.changes?.length ? `（${value.changes.length}）` : ''}</Button></div>
      {preparation && (needsThumbnails || clips.some(c => c.issue === 'preparing')) && <Alert type="warning" message="视频预览素材准备未完成，可重试；已就绪的视频仍可剪辑。" action={<Button disabled={disabled} onClick={() => void action(() => retry(preparation))}>重试检测</Button>}/>}
      <AssemblyWorkbench editor={editor} disabled={disabled} result={result} onResult={setResult} onPreview={() => void preview()} previewBusy={previewBusy || !!previewJob} focused={focused}/>
      {previewJob && <div className="assembly-render-status" role="status"><span>正在后台合成预览，可以继续编辑。</span><Button onClick={() => void action(async () => { await api.cancel(previewJob); setPreviewJob(null); })}>取消预览</Button></div>}
      {current && <div className="assembly-current"><span>当前成片{current.is_stale ? ' · 草稿已有新修改' : ''}</span><Button type="link" onClick={() => setResult(current)}>播放成片</Button><a href={api.download(current.id)}>下载 MP4</a></div>}
      {active && <div className="assembly-render-status" role="status"><div><strong>{active.cancel_requested ? '正在取消合成…' : stages[active.stage] ?? '正在合成'}</strong><span>可以继续编辑，当前导出使用提交时的版本。</span></div><Progress percent={active.progress} size="small"/><Button disabled={disabled || active.cancel_requested} onClick={() => void action(async () => { await api.cancel(active.id); await editor.load(); })}>取消导出</Button></div>}
      <footer className="assembly-export-bar"><div><strong>{included.length} 个片段 <span className="assembly-total">{timecode(total)}</span></strong><span>{blocked.length ? `${blocked.length} 个片段需要处理，或删除该片段后导出` : included.length ? '已准备好导出本集成片' : '请至少包含一个片段'}</span></div><div className="assembly-export-actions"><label>导出清晰度 <Select aria-label="导出清晰度" value={value.assembly.resolution} disabled={disabled} options={[{ value: '720p', label: '720p' }, { value: '1080p', label: '1080p' }]} onChange={resolution => editor.edit(state => ({ ...state, assembly: { ...state.assembly!, resolution } }))}/></label><Button type="primary" size="large" loading={busy} disabled={readOnly || !included.length || !!blocked.length || !!active || editor.status === 'error'} onClick={() => void action(prepareExport)}>导出成片</Button></div></footer>
    </>}
    {syncOpen && <Dialog title="同步分镜视频" canClose={!busy} onClose={() => setSyncOpen(false)}><p>保留轨道顺序和所有分割、裁剪。替换视频会更新该分镜的所有片段；新视频过短时需要重新调整裁剪。已删除的片段不会重新加入，新分镜追加到末尾。</p>{value?.changes?.length ? <ul>{value.changes.map(c => <li key={c.shot_id}>镜头 {c.position}：{({ added: '追加新片段', replacement: '更新采用的视频', archived: '不再参与导出' })[c.kind]}</li>)}</ul> : <p>分镜视频没有替换变化，可重新检测尚未完成的视频。</p>}<Button type="primary" loading={busy} onClick={() => void action(async () => { if (await editor.flush()) { editor.replace(await api.sync(editor.latest()!)); editor.clearHistory(); setSyncOpen(false); } })}>确认同步</Button></Dialog>}
    {exportOpen && <Dialog title="导出本集成片" canClose={!busy} onClose={() => setExportOpen(false)}><p>{included.length} 个片段，约 {timecode(total)}。导出 {value?.assembly?.resolution} MP4，保留 {value?.assembly?.aspect} 画幅。</p><p>合成在后台进行，完成后可预览、下载或设为当前成片。</p>{stale && <Checkbox checked={acknowledge} onChange={e => setAcknowledge(e.target.checked)}>部分片段对应旧分镜，已核对并继续使用这些视频</Checkbox>}<div className="assembly-dialog-actions"><Button onClick={() => setExportOpen(false)} disabled={busy}>返回编辑</Button><Button type="primary" loading={busy} disabled={!!blocked.length || (stale && !acknowledge)} onClick={() => void action(submitExport)}>开始合成</Button></div></Dialog>}
    {history && <Dialog title="导出记录" className="assembly-history-dialog" onClose={() => setHistory(null)}>{!history.length && <p>还没有导出记录。完成镜头编排后，点击“导出成片”。</p>}<div className="assembly-history">{history.map(job => <article key={job.id}><div><strong>{stages[job.stage] ?? job.status}</strong><time>{new Date(job.created_at.endsWith('Z') ? job.created_at : `${job.created_at}Z`).toLocaleString('zh-CN')}</time><span>{job.is_stale ? '与当前草稿不同' : '当前草稿版本'}{job.media_id === value?.assembly?.current_media_id ? ' · 当前成片' : ''}</span>{job.error && <p role="alert">{job.error.message}</p>}</div><div className="assembly-inline-actions">{job.status === 'succeeded' && <><Button onClick={() => { setResult(job); setHistory(null); }}>播放</Button><a href={api.download(job.id)}>下载</a><Button disabled={disabled || job.media_id === value?.assembly?.current_media_id} onClick={() => void action(async () => { if (!await editor.flush()) return; await editor.load(); setApplying({ ...job, is_stale: editor.latest()?.context_hash !== job.context_hash }); })}>设为当前成片</Button></>}{['failed', 'cancelled'].includes(job.status) && <Button disabled={disabled} onClick={() => void action(() => retry(job))}>重试</Button>}{['queued', 'running'].includes(job.status) && <><span>{job.progress}%</span><Button disabled={disabled || job.cancel_requested} onClick={() => void action(async () => { await api.cancel(job.id); await openHistory(); await editor.load(); })}>取消</Button></>}</div></article>)}</div><div className="assembly-dialog-actions"><Button disabled={busy} onClick={() => void action(openHistory)}>刷新记录</Button>{moreHistory && <Button disabled={busy} onClick={() => void action(async () => { const page = await api.history(history.length); setHistory([...history, ...page.items]); setMoreHistory(page.has_more); })}>加载更早记录</Button>}</div></Dialog>}
    {applying && <Dialog title="设为当前成片" canClose={!busy} onClose={() => setApplying(null)}><p>将采用这次导出的结果作为本集当前成片。其他导出记录仍会保留。</p>{applying.is_stale && <p>此结果与当前草稿不同，请确认画面和顺序。</p>}<Button type="primary" loading={busy} onClick={() => void action(async () => { if (!await editor.flush()) return; await api.apply(applying.id, editor.latest()!, applying.is_stale); setApplying(null); await editor.load(); if (history) await openHistory(); })}>确认采用</Button></Dialog>}
  </div>;
}
