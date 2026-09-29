import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Button, Slider, Tooltip } from 'antd';
import { Timeline, type TimelineState } from '@xzdarcy/react-timeline-editor';
import type { TimelineRow } from '@xzdarcy/timeline-engine';
import '@xzdarcy/react-timeline-editor/dist/react-timeline-editor.css';
import type { AssemblyClip } from '../../api/modules/assembly';
import { buildTimeline, FPS, framecode, moveClip, toFrame, trimClip } from './assembly-editing';
import { AssemblyFilmstrip } from './AssemblyFilmstrip';
import { adjacentStart, boundZoom, MAX_ZOOM, MIN_ZOOM, snapFrame, tickInterval, zoomScroll } from './timeline-view';

export function AssemblyTimeline({ clips, selected, frame, playing, disabled, onSelect, onSeek, onChange, onPause, onAdd, id, height = 152 }: {
  id?: string; height?: number;
  clips: AssemblyClip[]; selected: string; frame: number; playing: boolean; disabled: boolean;
  onSelect: (id: string) => void; onSeek: (frame: number) => void;
  onChange: (clips: AssemblyClip[]) => void; onPause: () => void;
  onAdd: (sourceId: string, index: number) => void;
}) {
  const timeline = useRef<TimelineState>(null);
  const container = useRef<HTMLDivElement>(null);
  const scroll = useRef(0);
  const zoomId = useId();
  const zoomTarget = useRef<number | null>(null);
  const [viewport, setViewport] = useState({ left: 0, width: 800 });
  const [feedback, setFeedback] = useState('');
  const [snapping, setSnapping] = useState(true);
  const [snapAt, setSnapAt] = useState<number | null>(null);
  const [inserting, setInserting] = useState<number | null>(null);
  const origin = useRef<{ id: string; start: number; end: number } | null>(null);
  const [scaleWidth, setScaleWidth] = useState(100);
  const [dropFrame, setDropFrame] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const entries = useMemo(() => buildTimeline(clips), [clips]);
  const total = entries.at(-1)?.end ?? 0;
  const starts = entries.map(entry => entry.start);
  const previous = adjacentStart(starts, frame, -1), next = adjacentStart(starts, frame, 1);
  function seekPointer(time: number) {
    const raw = Math.max(0, Math.min(total, time * FPS));
    const at = snapping ? snapFrame(raw, [...starts, total], scaleWidth) : Math.round(raw);
    setSnapAt(snapping && [...starts, total].includes(at) ? at : null);
    onSeek(at);
    // The library also updates its own cursor during dragging; override it after that event.
    requestAnimationFrame(() => timeline.current?.setTime(at / FPS));
  }
  const scale = tickInterval(scaleWidth);
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => setViewport(value => ({ ...value, width: entry.contentRect.width })));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    if (zoomTarget.current === null) return;
    timeline.current?.setScrollLeft(zoomTarget.current); zoomTarget.current = null;
  }, [scaleWidth]);
  function zoom(value: number, fit = false) {
    const next = boundZoom(value);
    zoomTarget.current = fit ? 0 : zoomScroll(frame / FPS, scaleWidth, next, scroll.current, viewport.width);
    if (next === scaleWidth) { timeline.current?.setScrollLeft(zoomTarget.current); zoomTarget.current = null; }
    else setScaleWidth(next);
  }
  const data = useMemo<TimelineRow[]>(() => [{ id: 'video', actions: entries.map(({ clip, start, end }) => ({
    id: clip.id, start: start / FPS, end: end / FPS, effectId: 'video', selected: clip.id === selected,
    movable: !disabled, flexible: !disabled && !!clip.duration_ms,
  })) }], [entries, selected, disabled, revision]);
  useEffect(() => {
    timeline.current?.setTime(frame / FPS);
    const x = 20 + frame / FPS * scaleWidth;
    const width = container.current?.clientWidth ?? 600;
    if (x > scroll.current + width - 65 || x < scroll.current) timeline.current?.setScrollLeft(Math.max(0, x - width * .3));
    if (playing) setSnapAt(null);
  }, [frame, playing, scaleWidth]);
  const begin = (id: string) => {
    onPause(); onSelect(id); setFeedback('');
    const entry = entries.find(e => e.clip.id === id);
    origin.current = entry ? { id, start: entry.start / FPS, end: entry.end / FPS } : null;
  };
  const trim = (id: string, start: number, end: number, dir: 'left' | 'right', commit: boolean) => {
    const entry = entries.find(e => e.clip.id === id), before = origin.current;
    if (!entry || !before) return false;
    const from = dir === 'left' ? entry.clip.trim_in_ms + (start - before.start) * 1000 : entry.clip.trim_in_ms;
    const to = dir === 'right' ? (entry.clip.trim_out_ms ?? entry.clip.duration_ms ?? 0) + (end - before.end) * 1000 : entry.clip.trim_out_ms ?? entry.clip.duration_ms ?? 0;
    if (toFrame(from) < 0 || toFrame(to) > toFrame(entry.clip.duration_ms ?? 0) || toFrame(to) - toFrame(from) < 1) {
      setFeedback('已到素材边界，且片段至少需要保留一帧。'); return false;
    }
    setFeedback(`镜头 ${entry.clip.shot_position} · 入点 ${framecode(toFrame(from))} → 出点 ${framecode(toFrame(to))} · 保留 ${framecode(toFrame(to) - toFrame(from))}${commit ? ' · 裁剪完成' : ''}`);
    if (commit && (toFrame(from) !== toFrame(entry.clip.trim_in_ms) || toFrame(to) !== toFrame(entry.clip.trim_out_ms ?? entry.clip.duration_ms ?? 0))) {
      onChange(trimClip(clips, id, from, to));
    }
    return true;
  };
  function insertion(at: number) {
    const entry = entries.find(e => at < (e.start + e.end) / 2);
    return entry ? clips.findIndex(c => c.id === entry.clip.id) : clips.length;
  }
  function dropTime(clientX: number) {
    return Math.max(0, Math.round((clientX - (container.current?.getBoundingClientRect().left ?? 0) - 20 + scroll.current) / scaleWidth * FPS));
  }
  return <section id={id} className="assembly-timeline" aria-label="视频时间轴">
    <div className="assembly-timeline-heading"><div><strong>视频轨道</strong><span>{entries.length} 个片段 · {framecode(total)} · 30 fps</span></div>
      <div className="assembly-zoom"><Button size="small" disabled={previous === undefined} onClick={() => { setSnapAt(null); onSeek(previous!); }}>上一段</Button>
        <Button size="small" disabled={next === undefined} onClick={() => { setSnapAt(null); onSeek(next!); }}>下一段</Button>
        <Button size="small" aria-pressed={snapping} onClick={() => { setSnapping(!snapping); setSnapAt(null); }}>{snapping ? '吸附已开启' : '吸附已关闭'}</Button>
        <Button size="small" onClick={() => zoom((viewport.width - 60) / Math.max(1, total / FPS), true)}>适应全部</Button>
        <Button size="small" aria-label="缩小时间轴" onClick={() => zoom(scaleWidth / 1.5)} disabled={scaleWidth <= MIN_ZOOM}>−</Button>
        <label htmlFor={zoomId}>缩放</label><Slider id={zoomId} aria-label="时间轴缩放" min={0} max={100} value={Math.log(scaleWidth / MIN_ZOOM) / Math.log(MAX_ZOOM / MIN_ZOOM) * 100} onChange={v => zoom(MIN_ZOOM * (MAX_ZOOM / MIN_ZOOM) ** (v / 100))}/>
        <Button size="small" aria-label="放大时间轴" onClick={() => zoom(scaleWidth * 1.5)} disabled={scaleWidth >= MAX_ZOOM}>+</Button></div>
    </div>
    <div className="assembly-timeline-canvas" ref={container}
      onDragOver={e => { if (!disabled && e.dataTransfer.types.includes('application/x-assembly-source')) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setDropFrame(dropTime(e.clientX)); } }}
      onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropFrame(null); }}
      onDrop={e => { const id = e.dataTransfer.getData('application/x-assembly-source'); if (id && !disabled) { e.preventDefault(); onAdd(id, insertion(dropTime(e.clientX))); } setDropFrame(null); }}>
      <Timeline ref={timeline} editorData={data} effects={{ video: { id: 'video', name: '视频' } }}
        style={{ width: '100%', height }} rowHeight={height - 64} scale={scale} scaleWidth={scaleWidth * scale} scaleSplitCount={scale === 1 ? 5 : 5}
        minScaleCount={Math.max(3, Math.ceil(total / FPS / scale) + 2)} startLeft={20} autoScroll dragLine={snapping} disableDrag={disabled}
        onScroll={({ scrollLeft }) => { scroll.current = scrollLeft; setViewport(value => value.left === scrollLeft ? value : { ...value, left: scrollLeft }); }}
        onChange={() => false}
        onClickTimeArea={time => { seekPointer(time); return false; }}
        onCursorDragStart={onPause} onCursorDrag={seekPointer} onCursorDragEnd={seekPointer}
        onClickActionOnly={(_, { action, time }) => { onSelect(action.id); seekPointer(time); }}
        onActionMoveStart={({ action }) => begin(action.id)}
        onActionMoving={({ action, start }) => {
          const target = entries.find(e => e.clip.id !== action.id && start * FPS < (e.start + e.end) / 2);
          setInserting(target?.start ?? total);
          setFeedback(target ? `松开后移到镜头 ${target.clip.shot_position} 前面` : '松开后移到轨道末尾');
        }}
        onActionMoveEnd={({ action, start }) => {
          const others = entries.filter(e => e.clip.id !== action.id);
          const target = others.find(e => start * FPS < (e.start + e.end) / 2);
          const remaining = clips.filter(c => c.id !== action.id);
          const next = moveClip(clips, action.id, target ? remaining.findIndex(c => c.id === target.clip.id) : clips.length - 1);
          onChange(next);
          setInserting(null); setFeedback(next === clips ? '顺序未改变。' : '顺序已调整，片段间隙自动收拢。');
          setRevision(v => v + 1);
        }}
        onActionResizeStart={({ action }) => begin(action.id)}
        onActionResizing={({ action, start, end, dir }) => trim(action.id, start, end, dir, false)}
        onActionResizeEnd={({ action, start, end, dir }) => { trim(action.id, start, end, dir, true); origin.current = null; setRevision(v => v + 1); }}
        getScaleRender={seconds => <span>{scale < 1 ? framecode(Math.round(seconds * FPS)) : framecode(Math.round(seconds * FPS)).slice(0, -3)}</span>}
        getActionRender={action => {
          const entry = entries.find(e => e.clip.id === action.id);
          if (!entry) return null;
          const c = entry.clip;
          return <Tooltip trigger={['hover', 'focus']} mouseEnterDelay={.4} title={<div className="assembly-clip-tooltip">
            <strong>镜头 {String(c.shot_position).padStart(2, '0')}{c.muted ? ' · 已静音' : ''}</strong>
            <div>保留时长 {framecode(entry.end - entry.start)}</div>
            <div>来源范围 {framecode(toFrame(c.trim_in_ms))} → {framecode(toFrame(c.trim_out_ms ?? c.duration_ms ?? 0))}</div>
            <div>成片位置 {framecode(entry.start)} → {framecode(entry.end)}</div>
          </div>}><button type="button" className={`assembly-track-clip${selected === c.id ? ' is-selected' : ''}${c.issue ? ' has-issue' : ''}`}
            aria-label={`片段 镜头 ${c.shot_position}，${framecode(entry.end - entry.start)}`} aria-pressed={selected === c.id}
            onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); onSelect(c.id); onSeek(entry.start); }
              if (!disabled && e.altKey && ['ArrowLeft', 'ArrowRight'].includes(e.key) && c.duration_ms) {
                e.preventDefault(); e.stopPropagation(); begin(c.id);
                const delta = (e.key === 'ArrowRight' ? 1 : -1) / FPS;
                trim(c.id, entry.start / FPS + (e.shiftKey ? delta : 0), entry.end / FPS + (e.shiftKey ? 0 : delta), e.shiftKey ? 'left' : 'right', true);
                origin.current = null;
              }
            }}
            onFocus={() => onSelect(c.id)}>
            <AssemblyFilmstrip clip={c} start={20 + entry.start / FPS * scaleWidth} width={(entry.end - entry.start) / FPS * scaleWidth} height={height - 72} viewport={viewport}/>
            <span className="assembly-track-label">{selected === c.id && <span className="assembly-track-selected" aria-hidden="true">已选中 · </span>}镜头 {String(c.shot_position).padStart(2, '0')}{c.muted ? ' · 静音' : ''}</span>
            <span className="assembly-track-time">{c.issue ? '需处理' : framecode(entry.end - entry.start)}</span>
          </button></Tooltip>;
        }}/>
      {dropFrame !== null && <div className="assembly-drop-hint">松开添加到 {framecode(Math.min(dropFrame, total))} 附近</div>}
      {inserting !== null && <div className="assembly-insert-marker" style={{ left: Math.max(2, Math.min(viewport.width - 4, 20 + inserting / FPS * scaleWidth - viewport.left)) }} aria-hidden="true"/>}
      {snapAt !== null && <div className="assembly-snap-marker" style={{ left: 20 + snapAt / FPS * scaleWidth - viewport.left }} aria-hidden="true"/>}
      {!entries.length && <p className="assembly-track-empty">把左侧分镜拖到这里，或点击“添加”</p>}
    </div>
    <p className="assembly-timeline-feedback" role="status">{snapAt !== null ? `已吸附片段边界 ${framecode(snapAt)}` : feedback || '拖动片段排序，拖动两端裁剪；悬停或聚焦片段查看详情。缩放围绕播放位置。'}</p>
    <p className="assembly-timeline-help">S 分割 · Delete 删除 · Ctrl / ⌘ Z 撤销 · 选中片段后，Alt + 左右键逐帧调整出点，加 Shift 调整入点。</p>
  </section>;
}
