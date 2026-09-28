import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, InputNumber, Progress, Select, Slider, Spin, Switch } from 'antd';
import { Dialog } from '../../../components/ui/Dialog';
import { Icon } from '../../../components/ui/Icon';
import type { AssemblyClip, RenderJob } from '../../../api/modules/assembly';
import { errorMessage } from '../../../api/http';
import { clipDuration, moveClip, nextPlayable, timecode } from '../../../features/projects/assembly-editing';
import { useAssembly } from '../../../features/projects/useAssembly';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import '../../../app/assembly.css';

const issueLabels = { missing: '缺少视频', preparing: '检测视频中', invalid: '视频不可用', trim: '裁剪超出时长' };
const stages: Record<string, string> = { queued: '等待合成', preparing: '准备视频', rendering: '合成片段', joining: '拼接成片', uploading: '保存成片', complete: '已完成', failed: '导出失败', cancelled: '已取消' };

export function AssemblyStage({ projectId, episodeId, readOnly, registerBarrier, onStoryboard }: {
  projectId: string; episodeId: string; readOnly: boolean; registerBarrier: (barrier: NavigationBarrier | null) => void; onStoryboard: () => void;
}) {
  const editor = useAssembly(projectId, episodeId, registerBarrier);
  const { value, api } = editor;
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [syncOpen, setSyncOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [history, setHistory] = useState<RenderJob[] | null>(null);
  const [moreHistory, setMoreHistory] = useState(false);
  const [result, setResult] = useState<RenderJob | null>(null);
  const [applying, setApplying] = useState<RenderJob | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [continuous, setContinuous] = useState(false);
  const continuousRef = useRef(false);
  const autoPlay = useRef(false);
  const dragId = useRef('');
  const request = useRef<{ body: string; key: string } | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  const clips = value?.clips ?? [];
  const clip = clips.find(c => c.id === selected) ?? clips[0];
  const included = clips.filter(c => c.included);
  const total = included.reduce((sum, c) => sum + clipDuration(c), 0);
  const blocked = included.filter(c => c.issue);
  const stale = included.some(c => c.is_stale);
  const active = value?.jobs?.find(j => j.kind === 'export' && ['queued', 'running'].includes(j.status));
  const preparation = value?.jobs?.find(j => j.kind === 'probe' && j.status === 'failed');
  const current = value?.jobs?.find(j => j.media_id && j.media_id === value?.assembly?.current_media_id);
  const disabled = readOnly || busy;

  async function action(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setNotice('');
    try { await work(); } catch (cause) { setNotice(errorMessage(cause)); } finally { setBusy(false); }
  }
  function changeClip(changes: Partial<AssemblyClip>) {
    if (!clip || disabled) return;
    editor.edit(state => ({ ...state, clips: state.clips!.map(c => c.id === clip.id ? { ...c, ...changes } : c) }));
  }
  function reorder(id: string, target: number) {
    if (disabled) return;
    stopPreview();
    editor.edit(state => ({ ...state, clips: moveClip(state.clips!, id, target) }));
  }
  function stopPreview() { continuousRef.current = false; autoPlay.current = false; setContinuous(false); video.current?.pause(); }
  function choose(id: string) { stopPreview(); setSelected(id); }
  function play() { void video.current?.play().catch(() => { stopPreview(); setNotice('浏览器暂停了自动播放，请点击播放器中的播放按钮。'); }); }
  function advance() {
    if (!continuousRef.current) return;
    const next = nextPlayable(clips, clip?.id);
    if (!next) { stopPreview(); return; }
    autoPlay.current = true; setSelected(next.id);
  }
  function startPreview() {
    const first = nextPlayable(clips);
    if (!first) return;
    continuousRef.current = true; setContinuous(true); autoPlay.current = true;
    if (first.id === clip?.id && video.current) { video.current.currentTime = first.trim_in_ms / 1000; play(); }
    else setSelected(first.id);
  }
  useEffect(() => { if (autoPlay.current && clip && video.current?.readyState && continuousRef.current) { video.current.currentTime = clip.trim_in_ms / 1000; play(); } }, [clip?.id]);
  useEffect(() => () => { continuousRef.current = false; }, []);
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

  return <div className="assembly-workspace">
    <header className="assembly-heading"><div><h2>成片合成与导出</h2><p>把本集镜头串成完整故事。</p></div><div className="assembly-heading-actions"><span role="status" className={`assembly-save is-${editor.status}`}>{({ loading: '正在载入', saved: '成片草稿已保存', saving: '正在保存', unsaved: '等待保存', error: '保存已暂停' })[editor.status]}</span>{value?.assembly && <Button onClick={() => void action(openHistory)}>导出记录</Button>}</div></header>
    {notice && <Alert type="error" showIcon message={notice} closable onClose={() => setNotice('')}/>}
    {editor.error && <Alert type="error" showIcon message={editor.error} description="当前编辑保留在本页。版本冲突时，请先下载草稿，再载入服务端版本。" action={<div className="assembly-inline-actions"><Button onClick={backup}>下载草稿</Button><Button disabled={busy} onClick={() => void action(async () => { await editor.retrySave(); })}>重试保存</Button><Button disabled={busy} onClick={() => { if (window.confirm('载入将替换本页编辑，是否继续？')) void action(editor.reload); }}>重新载入</Button></div>}/>}
    {!value && editor.status === 'loading' && <div className="assembly-empty"><Spin/><p>正在载入成片工作台…</p></div>}
    {value && !value.assembly && <div className="assembly-empty"><div className="assembly-empty-frame"><Icon name="film" size={40}/></div><h3>让分镜成为一部作品</h3><p>从已采用的分镜视频开始，调整顺序、保留精彩片段，再导出本集成片。</p><Button type="primary" disabled={disabled || !value.source_count} loading={busy} onClick={() => void action(async () => { editor.replace(await api.initialize()); })}>创建成片草稿</Button><Button type="link" onClick={onStoryboard}>{value.source_count ? '返回分镜制作' : '先去创建分镜'}</Button></div>}
    {value?.assembly && <>
      <div className="assembly-context"><span>{value.assembly.aspect} 画幅 <span aria-hidden="true"> / </span> {clips.length} 个片段</span><Button disabled={disabled} onClick={() => void action(async () => { if (await editor.flush()) { await editor.load(); setSyncOpen(true); } })}>同步分镜视频{value.changes?.length ? `（${value.changes.length}）` : ''}</Button></div>
      {preparation && clips.some(c => c.issue === 'preparing') && <Alert type="warning" message="视频检测未完成，重试后即可裁剪与导出。" action={<Button disabled={disabled} onClick={() => void action(() => retry(preparation))}>重试检测</Button>}/>}
      <div className="assembly-desk">
        <aside className="assembly-sequence" aria-label="成片片段顺序"><div className="assembly-panel-heading"><h3>镜头序列</h3><span>拖动或使用上下按钮排序</span></div>
          {!clips.length && <p className="assembly-list-empty">还没有分镜片段。<Button type="link" onClick={onStoryboard}>返回分镜制作</Button></p>}
          <ol>{clips.map((c, index) => <li key={c.id} className={`assembly-clip${clip?.id === c.id ? ' is-selected' : ''}${!c.included ? ' is-excluded' : ''}`} draggable={!disabled} onDragStart={event => { dragId.current = c.id; event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', c.id); }} onDragOver={event => { if (!disabled) event.preventDefault(); }} onDrop={event => { event.preventDefault(); reorder(dragId.current, index); }}>
            <div className="assembly-clip-top"><Checkbox aria-label={`包含镜头 ${c.shot_position}`} checked={c.included} disabled={disabled} onChange={event => editor.edit(state => ({ ...state, clips: state.clips!.map(item => item.id === c.id ? { ...item, included: event.target.checked } : item) }))}/><span>镜头 {String(c.shot_position).padStart(2, '0')}</span><span className="assembly-clip-duration">{c.duration_ms ? timecode(clipDuration(c)) : '—'}</span></div>
            <button type="button" className="assembly-clip-select" aria-pressed={clip?.id === c.id} onClick={() => choose(c.id)}><div className="assembly-thumbnail">{c.poster ? <img src={c.poster} alt="" loading="lazy"/> : <Icon name="film" size={22}/>}<span>{String(index + 1).padStart(2, '0')}</span></div><div><p>{c.script || '未填写分镜内容'}</p><small className={c.issue ? 'assembly-issue' : ''}>{!c.included ? '不参与导出' : c.issue ? issueLabels[c.issue] : c.is_stale ? '来源有更新，待核对' : c.muted ? '已静音' : '保留原声'}</small></div></button>
            <div className="assembly-reorder"><Button size="small" type="text" aria-label={`上移镜头 ${c.shot_position}`} disabled={disabled || index === 0} onClick={() => reorder(c.id, index - 1)}>上移</Button><Button size="small" type="text" aria-label={`下移镜头 ${c.shot_position}`} disabled={disabled || index === clips.length - 1} onClick={() => reorder(c.id, index + 1)}>下移</Button></div>
          </li>)}</ol>
        </aside>
        <section className="assembly-preview" aria-label="片段预览与裁剪"><div className="assembly-panel-heading"><h3>{clip ? `镜头 ${String(clip.shot_position).padStart(2, '0')}` : '成片预览'}</h3><Button disabled={!nextPlayable(clips)} onClick={continuous ? stopPreview : startPreview}>{continuous ? '停止连播' : '顺序预览'}</Button></div>
          <div className={`assembly-screen${value.assembly.aspect === '9:16' ? ' is-portrait' : ''}`}>{clip?.url ? <video key={clip.media_id} ref={video} src={clip.url} poster={clip.poster ?? undefined} controls playsInline preload="metadata" muted={clip.muted}
            onLoadedMetadata={() => { if (video.current) video.current.currentTime = clip.trim_in_ms / 1000; if (autoPlay.current && continuousRef.current) play(); }}
            onPlay={() => { if (video.current && (video.current.currentTime * 1000 < clip.trim_in_ms || video.current.currentTime * 1000 >= (clip.trim_out_ms ?? clip.duration_ms ?? Infinity))) video.current.currentTime = clip.trim_in_ms / 1000; }}
            onTimeUpdate={() => { const player = video.current; if (player && !player.paused && player.currentTime * 1000 >= (clip.trim_out_ms ?? clip.duration_ms ?? Infinity) - 30) { player.pause(); advance(); } }} onEnded={advance} onError={() => setNotice('视频暂时无法播放。请重新载入获取播放地址，或返回分镜制作检查视频。')}/>
            : <div className="assembly-no-video"><Icon name="film" size={36}/><p>{clip ? '这个镜头还没有可用视频' : '选择一个片段开始预览'}</p>{clip && <Button onClick={onStoryboard}>去分镜制作补齐</Button>}</div>}</div>
          {clip && <div className="assembly-trim"><div className="assembly-trim-title"><h3>保留片段</h3><span>{clip.duration_ms ? `原片 ${timecode(clip.duration_ms)} · 保留 ${timecode(clipDuration(clip))}` : '等待视频时长检测'}</span></div>
            <Slider range min={0} max={clip.duration_ms ?? 1} step={100} value={[clip.trim_in_ms, clip.trim_out_ms ?? clip.duration_ms ?? 1]} disabled={disabled || !clip.duration_ms} tooltip={{ formatter: value => timecode(Number(value)) }} onChange={([start, end]) => { if (end > start) { stopPreview(); changeClip({ trim_in_ms: start, trim_out_ms: end }); } }}/>
            <div className="assembly-trim-controls"><label>起点 <InputNumber aria-label="裁剪起点（秒）" min={0} max={Math.max(0, (clip.trim_out_ms ?? clip.duration_ms ?? 0) / 1000 - .1)} step={.1} precision={1} suffix="秒" value={clip.trim_in_ms / 1000} disabled={disabled || !clip.duration_ms} onChange={v => { if (v !== null) { stopPreview(); changeClip({ trim_in_ms: Math.round(v * 1000) }); } }}/></label><label>终点 <InputNumber aria-label="裁剪终点（秒）" min={clip.trim_in_ms / 1000 + .1} max={(clip.duration_ms ?? 0) / 1000} step={.1} precision={1} suffix="秒" value={(clip.trim_out_ms ?? clip.duration_ms ?? 0) / 1000} disabled={disabled || !clip.duration_ms} onChange={v => { if (v !== null) { stopPreview(); changeClip({ trim_out_ms: Math.round(v * 1000) }); } }}/></label><Button type="text" disabled={disabled || !clip.duration_ms} onClick={() => { stopPreview(); changeClip({ trim_in_ms: 0, trim_out_ms: null }); }}>重置裁剪</Button></div>
            <div className="assembly-trim-shortcuts"><Button size="small" disabled={disabled || !clip.duration_ms} onClick={() => { const at = Math.round((video.current?.currentTime ?? 0) * 1000); if (at < (clip.trim_out_ms ?? clip.duration_ms!)) { stopPreview(); changeClip({ trim_in_ms: at }); } }}>当前位置设为起点</Button><Button size="small" disabled={disabled || !clip.duration_ms} onClick={() => { const at = Math.min(clip.duration_ms!, Math.round((video.current?.currentTime ?? 0) * 1000)); if (at > clip.trim_in_ms) { stopPreview(); changeClip({ trim_out_ms: at }); } }}>当前位置设为终点</Button><label className="assembly-mute"><Switch aria-label="片段静音" checked={clip.muted} disabled={disabled} onChange={muted => changeClip({ muted })}/> 片段静音</label></div>
          </div>}
          <p className="assembly-preview-note">顺序预览用于检查镜头衔接，最终效果以导出的成片为准。</p>
        </section>
      </div>
      {current && <div className="assembly-current"><span>当前成片{current.is_stale ? ' · 草稿已有新修改' : ''}</span><Button type="link" onClick={() => setResult(current)}>播放成片</Button><a href={api.download(current.id)}>下载 MP4</a></div>}
      {active && <div className="assembly-render-status" role="status"><div><strong>{active.cancel_requested ? '正在取消合成…' : stages[active.stage] ?? '正在合成'}</strong><span>可以继续编辑，当前导出使用提交时的版本。</span></div><Progress percent={active.progress} size="small"/><Button disabled={disabled || active.cancel_requested} onClick={() => void action(async () => { await api.cancel(active.id); await editor.load(); })}>取消导出</Button></div>}
      <footer className="assembly-export-bar"><div><strong>{included.length} 个片段 <span className="assembly-total">{timecode(total)}</span></strong><span>{blocked.length ? `${blocked.length} 个片段需要处理，或取消勾选后导出` : included.length ? '已准备好导出本集成片' : '请至少包含一个片段'}</span></div><div className="assembly-export-actions"><label>导出清晰度 <Select aria-label="导出清晰度" value={value.assembly.resolution} disabled={disabled} options={[{ value: '720p', label: '720p' }, { value: '1080p', label: '1080p' }]} onChange={resolution => editor.edit(state => ({ ...state, assembly: { ...state.assembly!, resolution } }))}/></label><Button type="primary" size="large" loading={busy} disabled={readOnly || !included.length || !!blocked.length || !!active || editor.status === 'error'} onClick={() => void action(prepareExport)}>导出成片</Button></div></footer>
    </>}
    {syncOpen && <Dialog title="同步分镜视频" canClose={!busy} onClose={() => setSyncOpen(false)}><p>保留当前片段顺序。替换视频会重置对应裁剪，已归档分镜会取消勾选，新分镜追加到末尾。</p>{value?.changes?.length ? <ul>{value.changes.map(c => <li key={c.shot_id}>镜头 {c.position}：{({ added: '追加新片段', replacement: '更新采用的视频', archived: '不再参与导出' })[c.kind]}</li>)}</ul> : <p>分镜视频没有替换变化，可重新检测尚未完成的视频。</p>}<Button type="primary" loading={busy} onClick={() => void action(async () => { if (await editor.flush()) { editor.replace(await api.sync(editor.latest()!)); setSyncOpen(false); } })}>确认同步</Button></Dialog>}
    {exportOpen && <Dialog title="导出本集成片" canClose={!busy} onClose={() => setExportOpen(false)}><p>{included.length} 个片段，约 {timecode(total)}。导出 {value?.assembly?.resolution} MP4，保留 {value?.assembly?.aspect} 画幅。</p><p>合成在后台进行，完成后可预览、下载或设为当前成片。</p>{stale && <Checkbox checked={acknowledge} onChange={e => setAcknowledge(e.target.checked)}>部分片段对应旧分镜，已核对并继续使用这些视频</Checkbox>}<div className="assembly-dialog-actions"><Button onClick={() => setExportOpen(false)} disabled={busy}>返回编辑</Button><Button type="primary" loading={busy} disabled={!!blocked.length || (stale && !acknowledge)} onClick={() => void action(submitExport)}>开始合成</Button></div></Dialog>}
    {history && <Dialog title="导出记录" className="assembly-history-dialog" onClose={() => setHistory(null)}>{!history.length && <p>还没有导出记录。完成镜头编排后，点击“导出成片”。</p>}<div className="assembly-history">{history.map(job => <article key={job.id}><div><strong>{stages[job.stage] ?? job.status}</strong><time>{new Date(job.created_at.endsWith('Z') ? job.created_at : `${job.created_at}Z`).toLocaleString('zh-CN')}</time><span>{job.is_stale ? '与当前草稿不同' : '当前草稿版本'}{job.media_id === value?.assembly?.current_media_id ? ' · 当前成片' : ''}</span>{job.error && <p role="alert">{job.error.message}</p>}</div><div className="assembly-inline-actions">{job.status === 'succeeded' && <><Button onClick={() => setResult(job)}>播放</Button><a href={api.download(job.id)}>下载</a><Button disabled={disabled || job.media_id === value?.assembly?.current_media_id} onClick={() => void action(async () => { if (!await editor.flush()) return; await editor.load(); setApplying({ ...job, is_stale: editor.latest()?.context_hash !== job.context_hash }); })}>设为当前成片</Button></>}{['failed', 'cancelled'].includes(job.status) && <Button disabled={disabled} onClick={() => void action(() => retry(job))}>重试</Button>}{['queued', 'running'].includes(job.status) && <><span>{job.progress}%</span><Button disabled={disabled || job.cancel_requested} onClick={() => void action(async () => { await api.cancel(job.id); await openHistory(); await editor.load(); })}>取消</Button></>}</div></article>)}</div><div className="assembly-dialog-actions"><Button disabled={busy} onClick={() => void action(openHistory)}>刷新记录</Button>{moreHistory && <Button disabled={busy} onClick={() => void action(async () => { const page = await api.history(history.length); setHistory([...history, ...page.items]); setMoreHistory(page.has_more); })}>加载更早记录</Button>}</div></Dialog>}
    {result?.url && <Dialog title="成片预览" className="assembly-result-dialog" onClose={() => setResult(null)}><video src={result.url} controls playsInline autoPlay/><a href={api.download(result.id)}>下载 MP4</a></Dialog>}
    {applying && <Dialog title="设为当前成片" canClose={!busy} onClose={() => setApplying(null)}><p>将采用这次导出的结果作为本集当前成片。其他导出记录仍会保留。</p>{applying.is_stale && <p>此结果与当前草稿不同，请确认画面和顺序。</p>}<Button type="primary" loading={busy} onClick={() => void action(async () => { if (!await editor.flush()) return; await api.apply(applying.id, editor.latest()!, applying.is_stale); setApplying(null); await editor.load(); if (history) await openHistory(); })}>确认采用</Button></Dialog>}
  </div>;
}
