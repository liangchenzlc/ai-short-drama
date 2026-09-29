import { useId, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Button, InputNumber } from 'antd';
import type { AssemblyClip, RenderJob } from '../../api/modules/assembly';
import type { useAssembly } from './useAssembly';
import { AssemblyPlayer, type AssemblyPlayerHandle } from './AssemblyPlayer';
import { AssemblyResultPlayer } from './AssemblyResultPlayer';
import { AssemblyTimeline } from './AssemblyTimeline';
import { AssemblyResizeHandle } from './AssemblyResizeHandle';
import { useAssemblyLayout } from './useAssemblyLayout';
import { addClip, buildTimeline, FPS, framecode, locateFrame, moveClip, splitClip, toFrame } from './assembly-editing';

const issues = { missing: '缺少视频', preparing: '检测中', invalid: '视频不可用', trim: '裁剪超出时长' };

export function AssemblyWorkbench({ editor, disabled, result, onResult, onPreview, previewBusy, focused = false }: {
  editor: ReturnType<typeof useAssembly>; disabled: boolean; result: RenderJob | null;
  onResult: (result: RenderJob | null) => void; onPreview: () => void; previewBusy: boolean; focused?: boolean;
}) {
  const layout = useAssemblyLayout(focused);
  const layoutId = useId();
  const clips = editor.value?.clips ?? [];
  const [selected, setSelected] = useState('');
  const [frame, setFrame] = useState(0), [playing, setPlaying] = useState(false);
  const [notice, setNotice] = useState('');
  const player = useRef<AssemblyPlayerHandle>(null), resultVideo = useRef<HTMLVideoElement>(null);
  // The media bin follows storyboard order, independently of edits on the track.
  const sources = useMemo(() => [...(editor.value?.sources ?? [...new Map(clips.map(c => [c.shot_id, c])).values()])]
    .sort((a, b) => a.shot_position - b.shot_position), [editor.value?.sources, clips]);
  const selection = clips.find(c => c.id === selected) ?? clips.find(c => c.included);
  const entries = useMemo(() => buildTimeline(clips), [clips]);
  const total = entries.at(-1)?.end ?? 0;
  const selectedEntry = entries.find(e => e.clip.id === selection?.id);
  const splitEnabled = !!selectedEntry && frame > selectedEntry.start && frame < selectedEntry.end;
  const locked = disabled || !!result;
  const renderedClips = useMemo<AssemblyClip[]>(() => (result?.timeline ?? []).map((entry, index) => ({
    ...(sources.find(c => c.shot_id === entry.shot_id) ?? {}), id: entry.clip_id,
    shot_id: entry.shot_id, shot_position: sources.find(c => c.shot_id === entry.shot_id)?.shot_position ?? index + 1,
    media_id: null, position: index + 1, included: true, muted: entry.muted,
    trim_in_ms: entry.trim_in_ms, trim_out_ms: entry.trim_out_ms, duration_ms: entry.trim_out_ms,
    script: '', url: null, poster: null, filmstrip: null, issue: null, is_stale: false, archived: false,
  })), [result, sources]);
  function update(next: AssemblyClip[]) {
    if (locked || next === clips) return;
    player.current?.pause(); setNotice('');
    editor.edit(state => ({ ...state, clips: next }));
  }
  function add(id: string, index = clips.length) {
    const source = sources.find(s => s.id === id);
    if (!source || locked) return;
    if (clips.length >= 300) { setNotice('轨道最多支持 300 个片段，请先移除不需要的片段。'); return; }
    const next = addClip(clips, source, index);
    update(next); setSelected(next[index].id);
  }
  function split() {
    if (!selection || !splitEnabled || locked) return;
    if (clips.length >= 300) { setNotice('轨道最多支持 300 个片段，请先移除不需要的片段。'); return; }
    const id = crypto.randomUUID();
    update(splitClip(clips, selection.id, frame, id)); setSelected(id);
  }
  function remove() {
    if (!selection || locked) return;
    const index = clips.findIndex(c => c.id === selection.id);
    const next = clips.filter(c => c.id !== selection.id);
    update(next); setSelected(next[Math.min(index, next.length - 1)]?.id ?? '');
  }
  function seek(at: number) {
    const end = result ? buildTimeline(renderedClips).at(-1)?.end ?? toFrame(result.duration_ms ?? 0) : total;
    const next = Math.max(0, Math.min(at, end));
    if (result && resultVideo.current) { resultVideo.current.pause(); resultVideo.current.currentTime = next / FPS; setFrame(next); }
    else {
      const entry = locateFrame(entries, next);
      if (entry) setSelected(entry.clip.id);
      player.current?.seek(next);
    }
  }
  function select(id: string) { setSelected(id); }
  function history(action: 'undo' | 'redo') { if (!locked) { player.current?.pause(); editor[action](); } }
  const recent = editor.value?.jobs?.filter(j => j.kind === 'export' && j.status === 'succeeded') ?? [];
  return <div className={`assembly-editing${layout.layout.media ? '' : ' is-media-hidden'}`} style={{ '--assembly-player-height': `${layout.playerHeight}px` } as CSSProperties} tabIndex={-1} onKeyDown={event => {
    if ((event.target as HTMLElement).closest('input,textarea,select,[contenteditable=true],[role=dialog]')) return;
    const key = event.key.toLowerCase(), modifier = event.ctrlKey || event.metaKey;
    if (modifier && key === 'z') { event.preventDefault(); history(event.shiftKey ? 'redo' : 'undo'); }
    else if (modifier && key === 'y') { event.preventDefault(); history('redo'); }
    else if (!locked && key === 's' && !modifier) { event.preventDefault(); split(); }
    else if (!locked && ['delete', 'backspace'].includes(key)) { event.preventDefault(); remove(); }
    else if (event.code === 'Space' && event.target === event.currentTarget) { event.preventDefault(); player.current?.toggle(); }
  }}>
    <div className="assembly-layout-tools" role="group" aria-label="剪辑布局">
      <Button size="small" aria-expanded={layout.layout.media} aria-controls={`${layoutId}-media`} onClick={() => layout.update({ media: !layout.layout.media })}>{layout.layout.media ? '收起素材' : '展开素材'}</Button>
    </div>
    <div className="assembly-editor-desk">
      <aside id={`${layoutId}-media`} className="assembly-media-bin" aria-label="分镜视频素材" hidden={!layout.layout.media}>
        <div className="assembly-panel-heading"><h3>分镜视频</h3><span>{sources.length} 个</span></div>
        <p className="assembly-bin-help">拖入轨道，或点击添加</p>
        <div className="assembly-source-list">{sources.map(source => <article key={source.shot_id} draggable={!locked && !!source.url}
          onDragStart={event => { event.dataTransfer.setData('application/x-assembly-source', source.id); event.dataTransfer.effectAllowed = 'copy'; }}>
          <div className="assembly-source-image">{source.poster ? <img src={source.poster} alt={`镜头 ${source.shot_position}`} loading="lazy" draggable={false}/> : <span>镜头 {String(source.shot_position).padStart(2, '0')}</span>}<small>{framecode(toFrame(source.duration_ms ?? 0))}</small></div>
          <div className="assembly-source-meta"><strong>镜头 {String(source.shot_position).padStart(2, '0')}</strong><Button size="small" disabled={locked || !source.url || !!source.issue} aria-label={`添加镜头 ${source.shot_position}`} onClick={() => add(source.id)}>添加</Button></div>
          <p title={source.script}>{source.issue ? issues[source.issue] : source.script || '分镜视频'}</p>
        </article>)}</div>
      </aside>
      <div className="assembly-monitor" id={`${layoutId}-monitor`}>
        <div className="assembly-monitor-heading"><div className="assembly-mode-switch">
          <Button type={!result ? 'primary' : 'text'} onClick={() => { onResult(null); setFrame(0); setPlaying(false); }}>剪辑预览</Button>
          <Button type={result?.kind === 'export' ? 'primary' : 'text'} disabled={!recent.length} onClick={() => { player.current?.pause(); onResult(recent[0]); setFrame(0); }}>导出成片</Button>
        </div><Button disabled={disabled || !total || clips.some(c => c.included && c.issue)} loading={previewBusy} onClick={onPreview}>合成预览</Button></div>
        {result ? <AssemblyResultPlayer key={result.id} job={result} aspect={editor.value?.assembly?.aspect ?? '16:9'}
          stale={result.is_stale || result.context_hash !== editor.value?.context_hash || editor.status !== 'saved'}
          videoRef={resultVideo} refresh={() => editor.api.job(result.id)} onResult={onResult} onFrame={setFrame} onPlaying={setPlaying} download={editor.api.download(result.id)}/>
          : <AssemblyPlayer ref={player} clips={clips} aspect={editor.value?.assembly?.aspect ?? '16:9'} onFrame={at => { setFrame(at); if (playing) { const entry = locateFrame(entries, at); if (entry) setSelected(entry.clip.id); } }} onPlaying={setPlaying} onRefresh={editor.refreshMedia}/>}
      </div>
    </div>
    {layout.desktop && <AssemblyResizeHandle label="调整播放器高度" controls={`${layoutId}-monitor`} value={layout.playerHeight} min={200} max={layout.playerMax} onChange={playerHeight => layout.update({ playerHeight })}/>}
    <div className="assembly-edit-tools">
      <Button disabled={locked || !editor.canUndo} onClick={() => history('undo')}>撤销</Button><Button disabled={locked || !editor.canRedo} onClick={() => history('redo')}>重做</Button>
      <span className="assembly-tool-divider"/>
      <Button disabled={locked || !splitEnabled} onClick={split}>分割</Button><Button disabled={locked || !selection} onClick={remove}>删除片段</Button>
      <Button disabled={locked || !selection || clips.indexOf(selection) <= 0} onClick={() => selection && update(moveClip(clips, selection.id, clips.indexOf(selection) - 1))}>前移</Button>
      <Button disabled={locked || !selection || clips.indexOf(selection) >= clips.length - 1} onClick={() => selection && update(moveClip(clips, selection.id, clips.indexOf(selection) + 1))}>后移</Button>
      <Button disabled={locked || !selection} aria-pressed={selection?.muted ?? false} onClick={() => selection && update(clips.map(c => c.id === selection.id ? { ...c, muted: !c.muted } : c))}>{selection?.muted ? '恢复原声' : '片段静音'}</Button>
      <Button disabled={locked || !selection || (!selection.trim_in_ms && selection.trim_out_ms == null)} onClick={() => selection && update(clips.map(c => c.id === selection.id ? { ...c, trim_in_ms: 0, trim_out_ms: null } : c))}>恢复完整</Button>
      <label className="assembly-jump">定位 <InputNumber aria-label="定位时间（秒）" min={0} max={(result?.duration_ms ?? total / FPS * 1000) / 1000} step={1 / FPS} precision={3} value={frame / FPS} onChange={v => { if (v !== null) seek(Math.round(v * FPS)); }}/> 秒</label>
    </div>
    {notice && <p role="alert" className="assembly-edit-notice">{notice}</p>}
    <AssemblyTimeline id={`${layoutId}-track`} height={layout.desktop ? layout.layout.trackHeight : 152} clips={result ? renderedClips : clips} selected={result ? locateFrame(buildTimeline(renderedClips), frame)?.clip.id ?? '' : selection?.id ?? ''}
      frame={frame} playing={playing} disabled={locked} onSelect={select} onSeek={seek} onChange={update}
      onPause={() => { player.current?.pause(); resultVideo.current?.pause(); }} onAdd={add}/>
    {layout.desktop && <AssemblyResizeHandle label="调整轨道高度" controls={`${layoutId}-track`} value={layout.layout.trackHeight} min={152} max={360} onChange={trackHeight => layout.update({ trackHeight })}/>}
  </div>;
}
