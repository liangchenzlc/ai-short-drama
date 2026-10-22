import { useEffect, useId, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Button, Dropdown, InputNumber, Tooltip } from 'antd';
import { ArrowLeft, ArrowRight, AudioLines, Captions, Ellipsis, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Plus, Redo2, RotateCcw, Scissors, Trash2, Undo2, Volume2, VolumeX, WandSparkles } from 'lucide-react';
import { AssemblyAction } from './AssemblyAction';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import type { AssemblyClip, AssemblyMaterial, RenderJob } from '../../api/modules/assembly';
import type { AudioClip } from '../../api/modules/sound';
import { Dialog } from '../../components/ui/Dialog';
import type { useAssembly } from './useAssembly';
import { AssemblyPlayer, type AssemblyPlayerHandle } from './AssemblyPlayer';
import { AssemblyResultPlayer } from './AssemblyResultPlayer';
import { AssemblyTimeline } from './AssemblyTimeline';
import { AssemblyEditorPlayer } from './AssemblyEditorPlayer';
import { AssemblyMultitrack } from './AssemblyMultitrack';
import { AssemblyInspector } from './AssemblyInspector';
import { AssemblyMaterialDialog } from './AssemblyMaterialDialog';
import { AssemblyTranscriptionDialog } from './AssemblyTranscriptionDialog';
import { anchorAt, assemblyOutputAspect, materialClip, normalizeSound, splitAudio, type EditorSound } from './assembly-document';
import { splitSubtitle } from './assembly-subtitles';
import { useAssemblyAnalysis } from './useAssemblyAnalysis';
import { AssemblyResizeHandle } from './AssemblyResizeHandle';
import { useAssemblyLayout } from './useAssemblyLayout';
import { addClip, buildTimeline, canAddSource, clipDuration, clipFrames, FPS, framecode, isIncludedVideo, locateFrame, moveClip, renderedTimeline, splitClip, toFrame, toMs } from './assembly-editing';
import './standard-assembly.css';

const issues = { missing: '缺少视频', preparing: '检测中', invalid: '视频不可用', trim: '裁剪超出时长' };

export function AssemblyWorkbench({ editor, disabled, result, onResult, onPreview, previewBusy, previewProgress, focused = false, inert = false, soundOpen = false }: {
  editor: ReturnType<typeof useAssembly>; disabled: boolean; result: RenderJob | null;
  onResult: (result: RenderJob | null) => void; onPreview: () => void; previewBusy: boolean; previewProgress?: number; focused?: boolean; inert?: boolean; soundOpen?: boolean;
}) {
  const layout = useAssemblyLayout(focused);
  const layoutId = useId();
  const clips = editor.value?.clips ?? [];
  const [selected, setSelected] = useState('');
  const [selectedAux, setSelectedAux] = useState(''), [inspectorOpen, setInspectorOpen] = useState(false), [transcriptionOpen, setTranscriptionOpen] = useState(false);
  useEffect(() => { if (soundOpen) setInspectorOpen(false); }, [soundOpen]);
  const [inspectorWide, setInspectorWide] = useState(() => window.innerWidth > 1200);
  useEffect(() => { const query = window.matchMedia('(min-width: 1201px)'); const update = () => setInspectorWide(query.matches); query.addEventListener('change', update); return () => query.removeEventListener('change', update); }, []);
  const [materialOpen, setMaterialOpen] = useState<{ audio: boolean; replacing: boolean; kind: AudioClip['kind'] } | null>(null);
  const [shortReplacement, setShortReplacement] = useState<{ material: AssemblyMaterial; clip: AssemblyClip; start: number; end: number } | null>(null);
  const sound = normalizeSound(editor.value?.sound?.document, clips, editor.value?.sound?.media);
  const advanced = editor.value?.editor_version === 2 && !!editor.value.sound;
  const analysis = useAssemblyAnalysis(editor);
  const [frame, setFrame] = useState(0), [playing, setPlaying] = useState(false);
  const [notice, setNotice] = useState('');
  const [showCurrentWork, setShowCurrentWork] = useState(false);
  const currentWork = showCurrentWork && !result ? editor.value?.current_work : null;
  const player = useRef<AssemblyPlayerHandle>(null), resultVideo = useRef<HTMLVideoElement>(null);
  // The media bin follows storyboard order, independently of edits on the track.
  const sources = useMemo(() => {
    const shots = [...(editor.value?.sources ?? [...new Map(clips.map(c => [c.shot_id ?? c.media_id, c])).values()])].sort((a, b) => a.shot_position - b.shot_position);
    const materials = (editor.value?.materials ?? []).filter(m => m.kind !== 'audio' && !shots.some(c => c.media_id === m.media_id)).map(m => ({ ...materialClip(m), id: `material:${m.media_id}` }));
    return [...shots, ...materials];
  }, [editor.value?.sources, editor.value?.materials, clips]);
  const selection = clips.find(c => c.id === selected && c.media_id) ?? clips.find(isIncludedVideo);
  const entries = useMemo(() => buildTimeline(clips), [clips]);
  const total = entries.at(-1)?.end ?? 0;
  const selectedEntry = entries.find(e => e.clip.id === selection?.id);
  const auxiliary = sound.audio_clips.find(c => c.id === selectedAux) ?? sound.subtitles.find(c => c.id === selectedAux);
  const splitEnabled = auxiliary ? toMs(frame) > auxiliary.start_ms && toMs(frame) < ('duration_ms' in auxiliary ? auxiliary.start_ms + auxiliary.duration_ms : auxiliary.end_ms) : !!selectedEntry && frame > selectedEntry.start && frame < selectedEntry.end;
  const locked = disabled || soundOpen || !!result || !!currentWork;
  const selectionLocked = locked || (auxiliary ? sound.tracks.some(t => t.id === ('kind' in auxiliary ? auxiliary.kind : 'subtitles') && t.locked) : !!sound.picture_locked);
  const renderedClips = useMemo(() => result ? renderedTimeline(result, sources) : [], [result, sources]);
  const hiddenClips = clips.filter(c => isIncludedVideo(c) && clipFrames(c) === 0);
  useEffect(() => { setFrame(0); setPlaying(false); }, [result?.id, currentWork?.media_id]);
  function update(next: AssemblyClip[]) {
    if (locked || sound.picture_locked || next === clips) return;
    player.current?.pause(); setNotice('');
    editor.edit(state => ({ ...state, clips: next }));
  }
  function add(id: string, index = clips.length) {
    const source = sources.find(s => s.id === id);
    if (!source || locked || sound.picture_locked) return;
    if (!canAddSource(source)) { setNotice(`镜头 ${source.shot_position} ${source.issue ? issues[source.issue] : '尚不可播放'}，请等待检测完成或补齐视频后再添加。`); return; }
    if (clips.length >= 300) { setNotice('轨道最多支持 300 个片段，请先移除不需要的片段。'); return; }
    const next = addClip(clips, source, index);
    if (source.id.startsWith('material:')) next[index] = { ...next[index], source_clip_id: undefined };
    update(next); setSelected(next[index].id);
  }
  function split() {
    if (!splitEnabled || selectionLocked) return;
    if (auxiliary) { changeSound({ ...sound, ...('duration_ms' in auxiliary ? { audio_clips: splitAudio(sound.audio_clips, auxiliary.id!, toMs(frame), crypto.randomUUID()) } : { subtitles: splitSubtitle(sound.subtitles, auxiliary.id!, toMs(frame)) }) }); return; }
    if (!selection) return;
    if (clips.length >= 300) { setNotice('轨道最多支持 300 个片段，请先移除不需要的片段。'); return; }
    const id = crypto.randomUUID();
    update(splitClip(clips, selection.id, frame, id)); setSelected(id);
  }
  function remove(id = selection?.id) {
    if (auxiliary && id === selection?.id) { if (!selectionLocked) { changeSound({ ...sound, audio_clips: sound.audio_clips.filter(c => c.id !== selectedAux), subtitles: sound.subtitles.filter(s => s.id !== selectedAux) }); setSelectedAux(''); } return; }
    if (!id || locked || sound.picture_locked) return;
    const index = clips.findIndex(c => c.id === id);
    const next = clips.filter(c => c.id !== id).map((c, i) => ({ ...c, position: i + 1 }));
    update(next); setSelected(next.slice(index).find(isIncludedVideo)?.id ?? next.find(isIncludedVideo)?.id ?? '');
  }
  function seek(at: number) {
    const end = currentWork ? toFrame(currentWork.duration_ms ?? 0) : result ? buildTimeline(renderedClips).at(-1)?.end ?? toFrame(result.duration_ms ?? 0) : total;
    const next = Math.max(0, Math.min(at, end));
    if ((result || currentWork) && resultVideo.current) {
      const video = resultVideo.current;
      video.pause(); video.currentTime = Number.isFinite(video.duration) ? Math.min(next / FPS, video.duration) : next / FPS; setFrame(next);
    }
    else {
      const entry = locateFrame(entries, next);
      if (entry) setSelected(entry.clip.id);
      player.current?.seek(next);
    }
  }
  function select(id: string) { setSelected(id); setSelectedAux(''); }
  function selectAux(id: string) { setSelectedAux(id); setInspectorOpen(true); }
  function changeSound(next: EditorSound) { if (!locked) { player.current?.pause(); editor.edit(state => ({ ...state, sound: { ...state.sound!, document: next } })); } }
  function openMaterial(audio: boolean, replacing = false, kind: AudioClip['kind'] = 'music') { setMaterialOpen({ audio, replacing, kind }); }
  function replaceMaterial(material: AssemblyMaterial, clip: AssemblyClip, start = 0, end = Math.min(clipDuration(clip), material.duration_ms ?? clipDuration(clip))) {
    update(clips.map(c => c.id === clip.id ? { ...materialClip(material), id: c.id, position: c.position, transform: c.transform, trim_in_ms: start, trim_out_ms: material.kind === 'image' ? null : end, display_duration_ms: material.kind === 'image' ? clipDuration(c) : null, duration_ms: material.kind === 'image' ? clipDuration(c) : material.duration_ms } : c));
  }
  function chooseMaterial(material: AssemblyMaterial) {
    if (!materialOpen || locked) return;
    if (materialOpen.audio) {
      if (sound.tracks.some(t => t.id === materialOpen.kind && t.locked)) { setNotice('请先解锁对应声音轨'); return; }
      if (sound.audio_clips.length >= 600) { setNotice('声音片段最多支持 600 个'); return; }
      const start = toMs(frame), duration = material.duration_ms ?? 0;
      if (!duration) { setNotice('音频时长不可用，请重新导入'); return; }
      const id = crypto.randomUUID(), kind = materialOpen.kind;
      changeSound({ ...sound, audio_clips: [...sound.audio_clips, { id, name: material.name, media_id: material.media_id, kind, start_ms: start, duration_ms: Math.min(duration, 3600000 - start), trim_in_ms: 0, trim_out_ms: duration, volume: kind === 'music' ? .2 : 1, loop: false, fade_in_ms: kind === 'music' ? 500 : 0, fade_out_ms: kind === 'music' ? 500 : 0, ducking: kind === 'music', muted: false, gain_db: 0, ...(kind === 'music' ? {} : anchorAt(clips, start)) }] }); selectAux(id);
    } else if (sound.picture_locked) { setNotice('请先解锁画面轨'); return;
    } else if (materialOpen.replacing && selection) {
      if (material.kind === 'video' && (material.duration_ms ?? 0) < clipDuration(selection)) setShortReplacement({ material, clip: selection, start: 0, end: material.duration_ms ?? 0 });
      else replaceMaterial(material, selection);
    } else {
      if (clips.length >= 300) { setNotice('轨道最多支持 300 个片段'); return; }
      const clip = materialClip(material); update([...clips, { ...clip, position: clips.length + 1 }]); select(clip.id);
    }
  }
  function history(action: 'undo' | 'redo') { if (!locked) { player.current?.pause(); editor[action](); } }
  const recent = editor.value?.jobs?.filter(j => j.kind === 'export' && j.status === 'succeeded') ?? [];
  const inspector = advanced && <AssemblyInspector editor={editor} clip={selection} selectedAux={selectedAux} frame={frame} disabled={locked} onClose={() => setInspectorOpen(false)} onSelectAux={selectAux} onSeek={seek} onMaterial={openMaterial} onTranscribe={() => setTranscriptionOpen(true)} analysis={analysis}/>;
  return <div className={`assembly-editing${layout.layout.media ? '' : ' is-media-hidden'}${inspectorOpen && advanced && inspectorWide ? ' has-inspector' : ''}`} inert={inert ? true : undefined} data-player-sized={layout.layout.playerHeight > 0} style={{ '--assembly-player-height': `${layout.playerHeight}px` } as CSSProperties} tabIndex={-1} onKeyDown={event => {
    if ((event.target as HTMLElement).closest('input,textarea,select,[contenteditable=true],[role=dialog]')) return;
    const key = event.key.toLowerCase(), modifier = event.ctrlKey || event.metaKey;
    if (modifier && key === 'z') { event.preventDefault(); history(event.shiftKey ? 'redo' : 'undo'); }
    else if (modifier && key === 'y') { event.preventDefault(); history('redo'); }
    else if (!locked && key === 's' && !modifier) { event.preventDefault(); split(); }
    else if (!locked && ['delete', 'backspace'].includes(key)) { event.preventDefault(); remove(); }
    else if (event.code === 'Space' && event.target === event.currentTarget) { event.preventDefault(); player.current?.toggle(); }
  }}>
    <div className="assembly-edit-tools" role="group" aria-label="剪辑工具">
      <AssemblyAction label={layout.layout.media ? '收起素材' : '展开素材'} icon={layout.layout.media ? <PanelLeftClose size={16}/> : <PanelLeftOpen size={16}/>} aria-expanded={layout.layout.media} aria-controls={`${layoutId}-media`} onClick={() => layout.update({ media: !layout.layout.media })}/>
      <span className="assembly-tool-divider"/>
      <AssemblyAction label="撤销" icon={<Undo2 size={16}/>} disabled={locked || !editor.canUndo} onClick={() => history('undo')}/>
      <AssemblyAction label="重做" icon={<Redo2 size={16}/>} disabled={locked || !editor.canRedo} onClick={() => history('redo')}/>
      <span className="assembly-tool-divider"/>
      <AssemblyAction label="分割" icon={<Scissors size={16}/>} disabled={selectionLocked || !splitEnabled} onClick={split}/>
      <AssemblyAction label="删除片段" icon={<Trash2 size={16}/>} disabled={selectionLocked || !selection && !auxiliary} onClick={() => remove()}/>
      <AssemblyAction label={selection?.muted ? '恢复原声' : '片段静音'} icon={selection?.muted ? <VolumeX size={16}/> : <Volume2 size={16}/>} disabled={selectionLocked || !selection || !!selectedAux} aria-pressed={selection?.muted ?? false} onClick={() => selection && update(clips.map(c => c.id === selection.id ? { ...c, muted: !c.muted } : c))}/>
      {advanced && <><span className="assembly-tool-divider"/><AssemblyAction label="添加画面素材" icon={<Plus size={16}/>} disabled={locked || !!sound.picture_locked} onClick={() => openMaterial(false)}/><AssemblyAction label="添加声音素材" icon={<AudioLines size={16}/>} disabled={locked} onClick={() => openMaterial(true)}/><AssemblyAction label="语音识别字幕" icon={<Captions size={16}/>} disabled={locked || !total} onClick={() => setTranscriptionOpen(true)}/><AssemblyAction label={inspectorOpen ? '收起编辑设置' : '展开编辑设置'} icon={inspectorOpen ? <PanelRightClose size={16}/> : <PanelRightOpen size={16}/>} aria-expanded={inspectorOpen} onClick={() => setInspectorOpen(!inspectorOpen)}/></>}
      <Dropdown trigger={['click']} overlayClassName="standard-creation-popup" menu={{ items: [
        { key: 'previous', label: '前移片段', icon: <ArrowLeft size={15}/>, disabled: selectionLocked || !!selectedAux || !selection || clips.indexOf(selection) <= 0, onClick: () => selection && update(moveClip(clips, selection.id, clips.indexOf(selection) - 1)) },
        { key: 'next', label: '后移片段', icon: <ArrowRight size={15}/>, disabled: selectionLocked || !!selectedAux || !selection || clips.indexOf(selection) >= clips.length - 1, onClick: () => selection && update(moveClip(clips, selection.id, clips.indexOf(selection) + 1)) },
        { key: 'restore', label: '恢复完整', icon: <RotateCcw size={15}/>, disabled: selectionLocked || !!selectedAux || !selection || (!selection.trim_in_ms && selection.trim_out_ms == null), onClick: () => selection && update(clips.map(c => c.id === selection.id ? { ...c, trim_in_ms: 0, trim_out_ms: null } : c)) },
      ] }}><Button type="text" size="small" className="assembly-icon-action" icon={<Ellipsis size={17}/>} aria-label="更多片段操作" title="更多片段操作"/></Dropdown>
      <label className="assembly-jump">定位 <InputNumber aria-label="定位时间（秒）" min={0} max={(currentWork?.duration_ms ?? result?.duration_ms ?? total / FPS * 1000) / 1000} step={1 / FPS} precision={3} value={frame / FPS} onChange={v => { if (v !== null) seek(Math.round(v * FPS)); }}/> 秒</label>
    </div>
    <div className="assembly-editor-desk">
      <aside id={`${layoutId}-media`} className="assembly-media-bin" aria-label="分镜视频素材" hidden={!layout.layout.media}>
        <div className="assembly-panel-heading"><h3>{advanced ? '素材' : '分镜视频'}</h3><span>{sources.length} 个</span></div>
        <div className="assembly-source-list">{sources.map(source => <article key={source.id} title={source.script} draggable={!locked && !sound.picture_locked && canAddSource(source)}
          onDragStart={event => { if (locked || !canAddSource(source)) { event.preventDefault(); return; } event.dataTransfer.setData('application/x-assembly-source', source.id); event.dataTransfer.effectAllowed = 'copy'; }}>
          <div className="assembly-source-image">{source.poster ? <img src={source.poster} alt={source.shot_id ? `镜头 ${source.shot_position}` : source.script} loading="lazy" draggable={false}/> : <span>{source.shot_id ? `镜头 ${String(source.shot_position).padStart(2, '0')}` : source.script}</span>}<small>{framecode(toFrame(source.duration_ms ?? 0))}</small></div>
          <div className="assembly-source-meta"><strong>{source.shot_id ? `镜头 ${String(source.shot_position).padStart(2, '0')}` : source.script || '画面素材'}</strong><AssemblyAction label={source.shot_id ? `添加镜头 ${source.shot_position}` : `添加素材 ${source.script}`} icon={<Plus size={16}/>} disabled={locked || !!sound.picture_locked || !canAddSource(source)} onClick={() => add(source.id)}/></div>
          <p title={source.script}>{source.issue ? issues[source.issue] : source.script || '分镜视频'}</p>
        </article>)}</div>
      </aside>
      <div className="assembly-monitor" id={`${layoutId}-monitor`}>
        <div className="assembly-monitor-heading"><div className="assembly-mode-switch" role="group" aria-label="预览模式">
          <Button type={!result && !currentWork ? 'primary' : 'text'} onClick={() => { onResult(null); setShowCurrentWork(false); setFrame(0); setPlaying(false); }}>剪辑预览</Button>
          <Button type={result?.kind === 'export' || currentWork ? 'primary' : 'text'} disabled={!recent.length && !editor.value?.current_work} onClick={() => { player.current?.pause(); if (editor.value?.current_work) { onResult(null); setShowCurrentWork(true); } else onResult(recent[0]); setFrame(0); }}>成片回看</Button>
        </div><Tooltip title={previewBusy ? `合成预览${previewProgress === undefined ? '' : ` ${previewProgress}%`}` : '合成预览'}><Button className="assembly-preview-action" type="text" aria-label="合成预览" icon={<WandSparkles size={16}/>} disabled={disabled || !total || clips.some(c => isIncludedVideo(c) && c.issue)} loading={previewBusy} onClick={onPreview}>{previewBusy && previewProgress !== undefined ? `${previewProgress}%` : null}</Button></Tooltip></div>
        {result ? <AssemblyResultPlayer key={result.id} job={result} aspect={result.output_width && result.output_height ? `${result.output_width}:${result.output_height}` : result.aspect === '9:16' ? '9:16' : '16:9'}
          stale={result.is_stale || result.context_hash !== editor.value?.context_hash || editor.status !== 'saved'}
          videoRef={resultVideo} refresh={signal => editor.api.job(result.id, signal)} onResult={onResult} onFrame={setFrame} onPlaying={setPlaying} download={editor.api.download(result.id)}/>
          : currentWork ? <AssemblyResultPlayer key={currentWork.media_id} job={currentWork} aspect={assemblyOutputAspect(editor.value)} stale={currentWork.is_stale || editor.status !== 'saved'} videoRef={resultVideo}
            refresh={async signal => { const fresh = (await editor.api.get(signal)).current_work; if (!fresh) throw new Error('当前采用成片暂不可用。'); return fresh; }}
            onResult={work => { const latest = editor.latest(); if (latest) editor.replace({ ...latest, current_work: work }); }} onFrame={setFrame} onPlaying={setPlaying} download={editor.api.downloadCurrent()}/>
            : advanced ? <AssemblyEditorPlayer ref={player} clips={clips} sound={editor.value!.sound!} readOnly={locked || !!sound.picture_locked} font={editor.value?.subtitle_font} aspect={assemblyOutputAspect(editor.value)} onFrame={setFrame} onPlaying={setPlaying} onRefresh={editor.refreshMedia} onTransform={(id, transform) => update(clips.map(c => c.id === id ? { ...c, transform } : c))}/>
            : <AssemblyPlayer ref={player} clips={clips} aspect={assemblyOutputAspect(editor.value)} onFrame={at => { setFrame(at); if (playing) { const entry = locateFrame(entries, at); if (entry) setSelected(entry.clip.id); } }} onPlaying={setPlaying} onRefresh={editor.refreshMedia}/>}
      </div>
      {inspectorOpen && advanced && inspectorWide && inspector}
    </div>
    {layout.desktop && <AssemblyResizeHandle label="调整播放器高度" controls={`${layoutId}-monitor`} value={layout.playerHeight} min={200} max={layout.playerMax} onChange={playerHeight => layout.update({ playerHeight })}/>}
    {notice && <StudioFeedback type="error" noticeKey="assembly-edit-action" message={notice}/>}
    {analysis.error && <StudioFeedback type="warning" noticeKey="assembly-waveform" message={analysis.error} actions={<Button onClick={analysis.retry}>重试声音分析</Button>}/>}
    {!result && !currentWork && hiddenClips.length > 0 && <StudioFeedback type="warning" noticeKey="assembly-hidden-clips" quiet persistent message={`${hiddenClips.length} 个片段无法显示在时间轴`}>
      {hiddenClips.map(c => <p key={c.id} className="assembly-inline-actions">镜头 {c.shot_position} · {c.issue ? issues[c.issue] : '片段不足一帧'}，无法显示在时间轴。
        <Button size="small" disabled={locked} aria-label={`移除镜头 ${c.shot_position} 的问题片段`} onClick={() => remove(c.id)}>移除片段</Button></p>)}
    </StudioFeedback>}
    {advanced && !result && !currentWork ? <AssemblyMultitrack id={`${layoutId}-track`} height={layout.desktop ? Math.max(280, layout.layout.trackHeight) : 280} clips={clips} sound={sound} media={editor.value!.sound!.media} selected={selection?.id ?? ''} selectedAux={selectedAux} frame={frame} playing={playing} disabled={locked} onSelect={select} onSelectAux={selectAux} onSeek={seek} onPause={() => player.current?.pause()} onChange={update} onSoundChange={changeSound} onAdd={add} waveforms={analysis.waveforms}/>
      : !currentWork && <AssemblyTimeline id={`${layoutId}-track`} height={layout.desktop ? layout.layout.trackHeight : 152} clips={result ? renderedClips : clips} selected={result ? locateFrame(buildTimeline(renderedClips), frame)?.clip.id ?? '' : selection?.id ?? ''}
      frame={frame} playing={playing} disabled={locked} onSelect={select} onSeek={seek} onChange={update}
      onPause={() => { player.current?.pause(); resultVideo.current?.pause(); }} onAdd={add}/>}
    {layout.desktop && !currentWork && <AssemblyResizeHandle label="调整轨道高度" controls={`${layoutId}-track`} value={Math.max(advanced && !result ? 280 : 152, layout.layout.trackHeight)} min={advanced && !result ? 280 : 152} max={360} onChange={trackHeight => layout.update({ trackHeight })}/>}
    {inspectorOpen && advanced && !inspectorWide && <Dialog title="成片编辑设置" className="standard-creation-dialog assembly-inspector-dialog" onClose={() => setInspectorOpen(false)}>{inspector}</Dialog>}
    {materialOpen && <AssemblyMaterialDialog editor={editor} audio={materialOpen.audio} replacing={materialOpen.replacing} onClose={() => setMaterialOpen(null)} onChoose={chooseMaterial}/>}
    {transcriptionOpen && <AssemblyTranscriptionDialog editor={editor} clipId={selection?.id} disabled={locked} onClose={() => setTranscriptionOpen(false)}/>}
    {shortReplacement && <Dialog title="替换素材较短" className="standard-creation-dialog assembly-operation-dialog" onClose={() => setShortReplacement(null)}><p>新视频不足当前片段时长。请调整裁剪范围，确认后再替换。</p><video className="assembly-replacement-video" src={shortReplacement.material.url} controls preload="metadata"/>
      <div className="assembly-inspector-pair"><label>起点（秒）<InputNumber aria-label="替换裁剪起点" min={0} max={shortReplacement.end / 1000} value={shortReplacement.start / 1000} onChange={v => v !== null && setShortReplacement({ ...shortReplacement, start: Math.round(v * 1000) })}/></label><label>终点（秒）<InputNumber aria-label="替换裁剪终点" min={shortReplacement.start / 1000} max={(shortReplacement.material.duration_ms ?? 0) / 1000} value={shortReplacement.end / 1000} onChange={v => v !== null && setShortReplacement({ ...shortReplacement, end: Math.round(v * 1000) })}/></label></div>
      <footer><Button onClick={() => setShortReplacement(null)}>保留原片段</Button><Button type="primary" disabled={shortReplacement.end - shortReplacement.start < 34} onClick={() => { replaceMaterial(shortReplacement.material, shortReplacement.clip, shortReplacement.start, shortReplacement.end); setShortReplacement(null); }}>确认替换</Button></footer></Dialog>}
  </div>;
}
