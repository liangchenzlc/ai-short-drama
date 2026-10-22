import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, InputNumber, Segmented, Select, Slider, Tabs } from 'antd';
import { AudioLines, Captions, Download, Link2, Link2Off, Merge, Plus, RotateCcw, Scissors, Trash2, Upload, WandSparkles, X } from 'lucide-react';
import type { AssemblyClip, FrameTransform } from '../../api/modules/assembly';
import type { AudioClip, Subtitle } from '../../api/modules/sound';
import { errorMessage } from '../../api/http';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { confirmAction } from '../../components/ui/confirm';
import { AssemblyAction } from './AssemblyAction';
import { AudioWaveform } from './AssemblyMultitrack';
import { anchorAt, clipSpans, normalizeSound, resegmentSubtitles, type EditorSound } from './assembly-document';
import { downloadText, exportSrt, parseSrt, splitSubtitle, validateSubtitles } from './assembly-subtitles';
import { toMs } from './assembly-editing';
import type { useAssembly } from './useAssembly';
import type { useAssemblyAnalysis } from './useAssemblyAnalysis';

export function AssemblyInspector({ editor, clip, selectedAux, frame, disabled, onClose, onSelectAux, onSeek, onMaterial, onTranscribe, analysis }: {
  editor: ReturnType<typeof useAssembly>; clip?: AssemblyClip; selectedAux: string; frame: number; disabled: boolean; onClose: () => void;
  onSelectAux: (id: string) => void; onSeek: (frame: number) => void; onMaterial: (audio: boolean, replacing?: boolean, kind?: AudioClip['kind']) => void; onTranscribe: () => void; analysis: ReturnType<typeof useAssemblyAnalysis>;
}) {
  const [tab, setTab] = useState('picture'), [error, setError] = useState(''), [busy, setBusy] = useState(false), [offset, setOffset] = useState(0), [subtitleSearch, setSubtitleSearch] = useState('');
  const file = useRef<HTMLInputElement>(null), mutation = useRef(false);
  const clips = editor.value?.clips ?? [], doc = normalizeSound(editor.value?.sound?.document, clips, editor.value?.sound?.media), audio = doc.audio_clips.find(c => c.id === selectedAux), subtitle = doc.subtitles.find(c => c.id === selectedAux);
  useEffect(() => { if (audio) setTab('audio'); else if (subtitle) setTab('subtitles'); }, [selectedAux]);
  const pictureLocked = disabled || !!doc.picture_locked;
  const isTrackLocked = (id: AudioClip['kind'] | 'subtitles') => disabled || doc.tracks.some(t => t.id === id && t.locked);
  function change(next: EditorSound) { if (disabled) return; try { validateSubtitles(next.subtitles); setError(''); editor.edit(state => ({ ...state, sound: { ...state.sound!, document: next } })); } catch (cause) { setError(cause instanceof Error ? cause.message : errorMessage(cause)); } }
  function audioChange(patch: Partial<AudioClip>) { if (!audio || isTrackLocked(audio.kind)) return; const next = { ...audio, ...patch }; if (next.start_ms < 0 || next.start_ms + next.duration_ms > 3600000 || next.duration_ms <= 0 || next.trim_in_ms < 0 || next.trim_out_ms <= next.trim_in_ms || !next.loop && next.duration_ms > next.trim_out_ms - next.trim_in_ms + 1) { setError('声音裁剪范围或时长无效'); return; } const duration = editor.value?.sound?.media[audio.media_id]?.duration_ms; if (duration && next.trim_out_ms > duration) { setError('裁剪不能超出素材时长'); return; } change({ ...doc, audio_clips: doc.audio_clips.map(c => c.id === audio.id ? next : c) }); }
  function subtitleChange(patch: Partial<Subtitle>) { if (!subtitle || isTrackLocked('subtitles')) return; change({ ...doc, subtitles: doc.subtitles.map(s => s.id === subtitle.id ? { ...s, ...patch } : s).sort((a, b) => a.start_ms - b.start_ms) }); }
  function frameChange(patch: Partial<FrameTransform>) { if (!clip || pictureLocked) return; editor.edit(state => ({ ...state, clips: state.clips?.map(c => c.id === clip.id ? { ...c, transform: { fit: 'contain', scale: 1, x: .5, y: .5, ...c.transform, ...patch } } : c) })); }
  async function normalizeLoudness() {
    if (!audio || mutation.current || isTrackLocked(audio.kind)) return; mutation.current = true; setBusy(true); setError('');
    const original = { id: audio.id, media_id: audio.media_id, trim_in_ms: audio.trim_in_ms, trim_out_ms: audio.trim_out_ms };
    try {
      const result = await analysis.analyse(original), latest = editor.latest(), latestDoc = normalizeSound(latest?.sound?.document, latest?.clips ?? [], latest?.sound?.media), selected = latestDoc.audio_clips.find(c => c.id === original.id);
      if (!selected || selected.media_id !== original.media_id || selected.trim_in_ms !== original.trim_in_ms || selected.trim_out_ms !== original.trim_out_ms) throw new Error('声音来源或裁剪已变化，分析结果未覆盖当前编辑');
      editor.edit(state => ({ ...state, sound: { ...state.sound!, document: { ...latestDoc, audio_clips: latestDoc.audio_clips.map(c => c.id === original.id ? { ...c, gain_db: result.gain_db ?? 0 } : c) } } }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : errorMessage(cause)); } finally { mutation.current = false; setBusy(false); }
  }
  function addSubtitle() {
    const start = toMs(frame), next = doc.subtitles.find(s => s.start_ms >= start), end = Math.min(start + 2000, next?.start_ms ?? 3600000);
    if (end <= start || doc.subtitles.some(s => start < s.end_ms && end > s.start_ms)) { setError('当前位置已有字幕，请移动游标到空闲区间'); return; }
    const id = crypto.randomUUID(); change({ ...doc, burn_subtitles: true, subtitles: [...doc.subtitles, { id, text: '新字幕', start_ms: start, end_ms: end, ...anchorAt(clips, start) }].sort((a, b) => a.start_ms - b.start_ms) }); onSelectAux(id);
  }
  const t = { fit: 'contain', scale: 1, x: .5, y: .5, ...clip?.transform } as FrameTransform;
  const index = subtitle ? doc.subtitles.indexOf(subtitle) : -1, following = doc.subtitles[index + 1];
  const link = audio ?? subtitle;
  const linkControl = link && <AssemblyAction label={link.anchor_clip_id ? '解除镜头关联' : '关联当前位置镜头'} icon={link.anchor_clip_id ? <Link2 size={15}/> : <Link2Off size={15}/>} disabled={audio ? isTrackLocked(audio.kind) : isTrackLocked('subtitles')} aria-pressed={!!link.anchor_clip_id} onClick={() => {
    const patch = link.anchor_clip_id ? { anchor_clip_id: null, anchor_source_in_ms: null } : anchorAt(clips, link.start_ms);
    if (!link.anchor_clip_id && !patch.anchor_clip_id) { setError('当前位置没有画面片段可关联'); return; } if (audio) audioChange(patch); else subtitleChange(patch);
  }}/>;
  return <aside className="assembly-inspector" aria-label="成片编辑设置">
    <div className="assembly-inspector-heading"><strong>编辑设置</strong><AssemblyAction label="收起编辑设置" icon={<X size={15}/>} onClick={onClose}/></div>
    <Tabs size="small" activeKey={tab} onChange={setTab} items={[
      { key: 'picture', label: '画面', children: <div className="assembly-inspector-fields">
        {clip ? <><div className="assembly-inspector-object"><strong>{clip.script || (clip.shot_id ? `镜头 ${clip.shot_position}` : '画面素材')}</strong><Button size="small" disabled={pictureLocked} onClick={() => onMaterial(false, true)}>替换素材</Button></div>
          <label>画面适配<Segmented value={t.fit} aria-label="画面适配" options={[{ value: 'contain', label: '适应' }, { value: 'cover', label: '填充' }]} disabled={pictureLocked} onChange={fit => frameChange({ fit: fit as FrameTransform['fit'] })}/></label>
          <label>缩放<Slider aria-label="画面缩放" min={1} max={5} step={.01} disabled={pictureLocked} value={t.scale} onChange={scale => frameChange({ scale })}/></label>
          <label>水平位置<Slider aria-label="画面水平位置" min={0} max={1} step={.01} disabled={pictureLocked} value={t.x} onChange={x => frameChange({ x })}/></label>
          <label>垂直位置<Slider aria-label="画面垂直位置" min={0} max={1} step={.01} disabled={pictureLocked} value={t.y} onChange={y => frameChange({ y })}/></label>
          <div className="assembly-inspector-row"><span>构图</span><AssemblyAction label="重置构图" icon={<RotateCcw size={15}/>} disabled={pictureLocked} onClick={() => frameChange({ fit: 'contain', scale: 1, x: .5, y: .5 })}/></div>
          {clip.source_type === 'image' && <label>图片时长（秒）<InputNumber aria-label="图片展示时长" min={.1} max={3600} value={(clip.display_duration_ms ?? 5000) / 1000} disabled={pictureLocked} onChange={value => value && editor.edit(state => ({ ...state, clips: state.clips?.map(c => c.id === clip.id ? { ...c, duration_ms: Math.round(value * 1000), display_duration_ms: Math.round(value * 1000), trim_in_ms: 0, trim_out_ms: null } : c) }))}/></label>}
        </> : <div className="assembly-material-empty">在轨道中选择一个画面</div>}
      </div> },
      { key: 'audio', label: '声音', children: <div className="assembly-inspector-fields">
        <div className="assembly-inspector-row"><Button size="small" icon={<Plus size={14}/>} disabled={disabled} onClick={() => onMaterial(true, false, 'music')}>配乐</Button><Button size="small" icon={<Plus size={14}/>} disabled={disabled} onClick={() => onMaterial(true, false, 'effect')}>音效</Button><Button size="small" icon={<Plus size={14}/>} disabled={disabled} onClick={() => onMaterial(true, false, 'dialogue')}>对白</Button></div>
        <Select aria-label="选择声音片段" value={audio?.id} placeholder="选择声音片段" onChange={onSelectAux} options={doc.audio_clips.map(c => ({ value: c.id, label: c.name }))}/>
        {audio ? <fieldset disabled={isTrackLocked(audio.kind)} className="assembly-inspector-fields"><div className="assembly-inspector-row"><Input aria-label="声音名称" value={audio.name} maxLength={255} onChange={event => audioChange({ name: event.target.value })}/>{linkControl}<AssemblyAction label="删除声音" icon={<Trash2 size={15}/>} onClick={() => { change({ ...doc, audio_clips: doc.audio_clips.filter(c => c.id !== audio.id) }); onSelectAux(''); }}/></div>
          <div className="assembly-waveform-preview"><AudioWaveform values={analysis.waveforms[audio.media_id]} clip={audio} duration={editor.value?.sound?.media[audio.media_id]?.duration_ms}/></div>
          <label>开始（秒）<InputNumber aria-label="声音开始时间" min={0} max={3600} step={.1} value={audio.start_ms / 1000} onChange={value => value !== null && audioChange({ start_ms: Math.round(value * 1000), ...(audio.anchor_clip_id ? anchorAt(clips, Math.round(value * 1000)) : {}) })}/></label>
          <div className="assembly-inspector-pair"><label>裁剪起点<InputNumber aria-label="声音裁剪起点" min={0} step={.1} value={audio.trim_in_ms / 1000} onChange={value => value !== null && audioChange({ trim_in_ms: Math.round(value * 1000), duration_ms: audio.loop ? audio.duration_ms : audio.trim_out_ms - Math.round(value * 1000) })}/></label><label>裁剪终点<InputNumber aria-label="声音裁剪终点" min={.001} step={.1} value={audio.trim_out_ms / 1000} onChange={value => value !== null && audioChange({ trim_out_ms: Math.round(value * 1000), duration_ms: audio.loop ? audio.duration_ms : Math.round(value * 1000) - audio.trim_in_ms })}/></label></div>
          <label>音量<Slider aria-label="声音音量" min={0} max={2} step={.01} value={audio.volume} onChange={volume => audioChange({ volume })}/></label>
          <div className="assembly-inspector-pair"><label>淡入（秒）<InputNumber aria-label="声音淡入" min={0} max={audio.duration_ms / 1000} step={.1} value={audio.fade_in_ms / 1000} onChange={v => v !== null && audioChange({ fade_in_ms: Math.round(v * 1000) })}/></label><label>淡出（秒）<InputNumber aria-label="声音淡出" min={0} max={audio.duration_ms / 1000} step={.1} value={audio.fade_out_ms / 1000} onChange={v => v !== null && audioChange({ fade_out_ms: Math.round(v * 1000) })}/></label></div>
          <div className="assembly-inspector-row"><Checkbox checked={audio.muted} onChange={e => audioChange({ muted: e.target.checked })}>静音</Checkbox><Checkbox checked={audio.loop} onChange={e => audioChange({ loop: e.target.checked, duration_ms: e.target.checked ? Math.max(audio.duration_ms, (clipSpans(clips).at(-1)?.end ?? 0) - audio.start_ms) : audio.trim_out_ms - audio.trim_in_ms })}>循环</Checkbox></div>
          {audio.loop && <label>循环时长（秒）<InputNumber aria-label="声音循环时长" min={.001} max={(3600000 - audio.start_ms) / 1000} value={audio.duration_ms / 1000} onChange={v => v !== null && audioChange({ duration_ms: Math.round(v * 1000) })}/></label>}
          {audio.kind === 'music' && <Checkbox checked={audio.ducking} onChange={event => audioChange({ ducking: event.target.checked })}>人声时自动压低配乐</Checkbox>}
          <div className="assembly-inspector-row"><Button size="small" icon={<WandSparkles size={14}/>} loading={busy} onClick={() => { void normalizeLoudness(); }}>统一响度</Button><span>{audio.gain_db.toFixed(1)} dB</span><AssemblyAction label="重置响度增益" icon={<RotateCcw size={14}/>} onClick={() => audioChange({ gain_db: 0 })}/></div>
        </fieldset> : <div className="assembly-material-empty">选择声音轨中的片段以调整</div>}
      </div> },
      { key: 'subtitles', label: '字幕', children: <div className="assembly-inspector-fields">
        <div className="assembly-inspector-row"><Checkbox checked={doc.burn_subtitles} disabled={disabled} onChange={e => change({ ...doc, burn_subtitles: e.target.checked })}>预览与导出字幕</Checkbox><AssemblyAction label="语音识别字幕" icon={<AudioLines size={15}/>} disabled={disabled} onClick={onTranscribe}/><AssemblyAction label="添加字幕" icon={<Plus size={15}/>} disabled={isTrackLocked('subtitles')} onClick={addSubtitle}/><AssemblyAction label="导入 SRT" icon={<Upload size={15}/>} disabled={isTrackLocked('subtitles')} onClick={() => file.current?.click()}/><AssemblyAction label="导出 SRT" icon={<Download size={15}/>} disabled={!doc.subtitles.length} onClick={() => downloadText(exportSrt(doc.subtitles), 'subtitles.srt')}/></div>
        <input ref={file} type="file" hidden accept=".srt" aria-label="导入成片 SRT" onChange={event => {
          const selectedFile = event.target.files?.[0]; event.target.value = ''; if (!selectedFile) return;
          const original = JSON.stringify(editor.latest()?.sound?.document.subtitles);
          void (async () => { try {
            if (selectedFile.size > 2 * 1024 ** 2) throw new Error('SRT 不能超过 2 MiB');
            const parsed = parseSrt(new TextDecoder('utf-8', { fatal: true }).decode(await selectedFile.arrayBuffer()));
            if (doc.subtitles.length && !await confirmAction('导入将替换现有字幕，可撤销本次操作。', { className: 'standard-creation-dialog' })) return;
            const latest = editor.latest();
            if (!latest?.sound || JSON.stringify(latest.sound.document.subtitles) !== original) throw new Error('字幕已变化，请重新导入；当前编辑已保留');
            const currentDoc = normalizeSound(latest.sound.document, latest.clips ?? [], latest.sound.media);
            if (disabled || currentDoc.tracks.some(t => t.id === 'subtitles' && t.locked)) return;
            change({ ...currentDoc, burn_subtitles: true, subtitles: parsed.map(s => ({ ...s, ...anchorAt(latest.clips ?? [], s.start_ms) })) });
          } catch (cause) { setError(cause instanceof Error ? cause.message : errorMessage(cause)); } })();
        }}/>
        <Input aria-label="搜索字幕" placeholder="搜索字幕" value={subtitleSearch} onChange={event => setSubtitleSearch(event.target.value)}/>
        <div className="assembly-caption-list">{doc.subtitles.filter(s => s.text.includes(subtitleSearch)).map((s, i) => <button key={s.id} className={selectedAux === s.id ? 'is-selected' : ''} onClick={() => { onSelectAux(s.id!); onSeek(Math.round(s.start_ms * 30 / 1000)); }}><small>{i + 1} · {(s.start_ms / 1000).toFixed(2)}s</small><span>{s.text}</span></button>)}{!doc.subtitles.length && <div className="assembly-material-empty">识别人声、导入 SRT 或添加字幕</div>}</div>
        {subtitle && <fieldset disabled={isTrackLocked('subtitles')} className="assembly-inspector-fields"><div className="assembly-inspector-row"><Captions size={15}/>{linkControl}<AssemblyAction label="分割字幕" icon={<Scissors size={15}/>} disabled={toMs(frame) <= subtitle.start_ms || toMs(frame) >= subtitle.end_ms || subtitle.text.length < 2} onClick={() => change({ ...doc, subtitles: splitSubtitle(doc.subtitles, subtitle.id!, toMs(frame)) })}/><AssemblyAction label="合并下一条字幕" icon={<Merge size={15}/>} disabled={!following} onClick={() => { if (following) { const merged = { ...subtitle, text: subtitle.text + '\n' + following.text, end_ms: following.end_ms, ...(subtitle.anchor_clip_id === following.anchor_clip_id ? {} : { anchor_clip_id: null, anchor_source_in_ms: null }) }; change({ ...doc, subtitles: doc.subtitles.filter(s => s.id !== following.id).map(s => s.id === subtitle.id ? merged : s) }); } }}/><AssemblyAction label="删除字幕" icon={<Trash2 size={15}/>} onClick={() => { change({ ...doc, subtitles: doc.subtitles.filter(s => s.id !== subtitle.id) }); onSelectAux(''); }}/></div>
          <Input.TextArea aria-label="校对字幕" value={subtitle.text} autoSize={{ minRows: 2, maxRows: 5 }} maxLength={1000} onChange={event => {
            const text = event.target.value;
            editor.edit(state => ({ ...state, sound: { ...state.sound!, document: { ...state.sound!.document, subtitles: state.sound!.document.subtitles.map(s => s.id === subtitle.id ? { ...s, text } : s) } } }));
            setError(text.trim() ? '' : '字幕不能为空，输入已保留，请补全后保存');
          }}/>
          <div className="assembly-inspector-pair"><label>开始（秒）<InputNumber aria-label="字幕开始时间" min={0} step={.1} value={subtitle.start_ms / 1000} onChange={v => v !== null && subtitleChange({ start_ms: Math.round(v * 1000), ...(subtitle.anchor_clip_id ? anchorAt(clips, Math.round(v * 1000)) : {}) })}/></label><label>结束（秒）<InputNumber aria-label="字幕结束时间" min={0} step={.1} value={subtitle.end_ms / 1000} onChange={v => v !== null && subtitleChange({ end_ms: Math.round(v * 1000) })}/></label></div>
        </fieldset>}
        <div className="assembly-inspector-row"><InputNumber aria-label="全部字幕偏移毫秒" value={offset} step={100} onChange={v => setOffset(v ?? 0)} addonAfter="ms"/><Button size="small" disabled={isTrackLocked('subtitles') || !doc.subtitles.length || !offset} onClick={() => change({ ...doc, subtitles: doc.subtitles.map(s => ({ ...s, start_ms: s.start_ms + offset, end_ms: s.end_ms + offset, ...(s.anchor_clip_id ? anchorAt(clips, s.start_ms + offset) : {}) })) })}>整体偏移</Button><AssemblyAction label="按标点自动断句" icon={<WandSparkles size={15}/>} disabled={isTrackLocked('subtitles') || !doc.subtitles.length} onClick={() => change({ ...doc, subtitles: resegmentSubtitles(doc.subtitles) })}/></div>
        <details className="assembly-subtitle-style"><summary>字幕样式</summary><div className="assembly-inspector-fields">
          <label>字号<InputNumber aria-label="字幕字号" min={12} max={72} value={doc.font_size} disabled={disabled} onChange={v => v !== null && change({ ...doc, font_size: v })}/></label>
          <label>位置<Select aria-label="字幕位置" value={doc.subtitle_style.alignment} disabled={disabled} onChange={alignment => change({ ...doc, subtitle_style: { ...doc.subtitle_style, alignment } })} options={['左下', '居中下', '右下', '左中', '居中', '右中', '左上', '居中上', '右上'].map((label, i) => ({ label, value: i + 1 }))}/></label>
          <div className="assembly-inspector-pair"><label>文字色<input aria-label="字幕文字颜色" type="color" value={doc.subtitle_style.color} disabled={disabled} onChange={e => change({ ...doc, subtitle_style: { ...doc.subtitle_style, color: e.target.value } })}/></label><label>描边色<input aria-label="字幕描边颜色" type="color" value={doc.subtitle_style.outline_color} disabled={disabled} onChange={e => change({ ...doc, subtitle_style: { ...doc.subtitle_style, outline_color: e.target.value } })}/></label></div>
          <label>描边<Slider aria-label="字幕描边宽度" min={0} max={8} step={.5} value={doc.subtitle_style.outline_width} disabled={disabled} onChange={outline_width => change({ ...doc, subtitle_style: { ...doc.subtitle_style, outline_width } })}/></label>
          <label>边距<Slider aria-label="字幕安全边距" min={0} max={30} step={1} value={doc.subtitle_style.margin_percent} disabled={disabled} onChange={margin_percent => change({ ...doc, subtitle_style: { ...doc.subtitle_style, margin_percent } })}/></label>
          <div className="assembly-inspector-row"><Checkbox checked={doc.subtitle_style.bold} disabled={disabled} onChange={e => change({ ...doc, subtitle_style: { ...doc.subtitle_style, bold: e.target.checked } })}>加粗</Checkbox><Checkbox checked={doc.subtitle_style.background} disabled={disabled} onChange={e => change({ ...doc, subtitle_style: { ...doc.subtitle_style, background: e.target.checked } })}>底色</Checkbox><input aria-label="字幕底色" type="color" disabled={disabled} value={doc.subtitle_style.background_color} onChange={e => change({ ...doc, subtitle_style: { ...doc.subtitle_style, background_color: e.target.value } })}/></div>
          {doc.subtitle_style.background && <label>底色透明度<Slider aria-label="字幕底色透明度" min={0} max={1} step={.05} disabled={disabled} value={doc.subtitle_style.background_opacity} onChange={background_opacity => change({ ...doc, subtitle_style: { ...doc.subtitle_style, background_opacity } })}/></label>}
        </div></details>
      </div> },
    ]}/>
    {error && <StudioFeedback type="error" noticeKey="assembly-inspector" message={error}/>}
  </aside>;
}
