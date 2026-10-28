import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import { waveformSlice } from '@host/features/creation/assembly/player/assembly-playback';
import { AssemblyFilmstrip } from '@host/features/creation/assembly/player/AssemblyFilmstrip';
import { anchorAt } from '@host/features/creation/assembly/sessions/assembly-document';
import {
  buildTimeline,
  FPS,
  framecode,
  moveClip,
  snapMs,
  trimClip,
} from '@host/features/creation/assembly/sessions/assembly-editing';
import type { AssemblyClip } from '@host/types/creation/assembly/document';
import type { EditorSound } from '@host/types/creation/assembly/sessions/assembly-document';
import type { AudioClip, SoundTrack, Subtitle } from '@host/types/creation/sound/tracks';
import { Timeline, type TimelineState } from '@xzdarcy/react-timeline-editor';
import type { TimelineRow } from '@xzdarcy/timeline-engine';
import { Slider } from 'antd';
import {
  Captions,
  Layers,
  LockKeyhole,
  Magnet,
  Maximize2,
  Minus,
  Plus,
  UnlockKeyhole,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
const kinds = ['original', 'dialogue', 'subtitles', 'music', 'effect'] as const;
const names = {
  original: '原声',
  dialogue: '对白',
  subtitles: '字幕',
  music: '配乐',
  effect: '音效',
};
export function AudioWaveform({
  values: source,
  clip,
  duration,
}: {
  values?: number[];
  clip?: AudioClip;
  duration?: number;
}) {
  const values = source && clip && duration ? waveformSlice(source, clip, duration) : source;
  return values?.length ? (
    <svg
      className="assembly-waveform"
      viewBox={`0 0 ${values.length} 32`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path
        d={values.map((v, i) => `M${i} ${16 - v * 15}v${Math.max(0.6, v * 30)}`).join(' ')}
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      />
    </svg>
  ) : null;
}
export function AssemblyMultitrack({
  id,
  clips,
  sound,
  media,
  selected,
  selectedAux,
  frame,
  playing,
  disabled,
  height,
  onSelect,
  onSelectAux,
  onSeek,
  onPause,
  onChange,
  onSoundChange,
  onAdd,
  waveforms,
}: {
  id: string;
  clips: AssemblyClip[];
  sound: EditorSound;
  selected: string;
  selectedAux: string;
  frame: number;
  playing: boolean;
  disabled: boolean;
  height: number;
  onSelect: (id: string) => void;
  onSelectAux: (id: string) => void;
  onSeek: (frame: number) => void;
  onPause: () => void;
  onChange: (clips: AssemblyClip[]) => void;
  onSoundChange: (sound: EditorSound) => void;
  onAdd: (id: string, index: number) => void;
  waveforms: Record<string, number[]>;
  media: Record<string, { duration_ms: number }>;
}) {
  const timeline = useRef<TimelineState>(null),
    viewport = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(70),
    [snapping, setSnapping] = useState(true),
    [scroll, setScroll] = useState({ left: 0, top: 0 });
  const [revision, setRevision] = useState(0),
    [feedback, setFeedback] = useState('');
  const [hideEmpty, setHideEmpty] = useState(false);
  const origin = useRef<{ id: string; start: number; end: number } | null>(null);
  const entries = useMemo(() => buildTimeline(clips), [clips]);
  const total = entries.at(-1)?.end ?? 0;
  const allAux: (AudioClip | Subtitle)[] = [...sound.audio_clips, ...sound.subtitles];
  const track = (kind: SoundTrack['id']) =>
    sound.tracks.find((t) => t.id === kind) ?? {
      id: kind,
      muted: false,
      locked: false,
    };
  const visibleKinds = kinds.filter(
    (kind) =>
      (!hideEmpty && kind !== 'dialogue') ||
      (kind === 'subtitles' ? sound.subtitles.length > 0 : sound.audio_clips.some((c) => c.kind === kind)),
  );
  const rows = useMemo<TimelineRow[]>(
    () => [
      {
        id: 'video',
        actions: entries.map((e) => ({
          id: e.clip.id,
          start: e.start / FPS,
          end: e.end / FPS,
          effectId: 'video',
          selected: selected === e.clip.id && !selectedAux,
          movable: !disabled && !sound.picture_locked,
          flexible: !disabled && !sound.picture_locked,
        })),
      },
      ...visibleKinds.map((kind) => ({
        id: kind,
        actions: (kind === 'subtitles' ? sound.subtitles : sound.audio_clips.filter((c) => c.kind === kind)).map(
          (c) => ({
            id: c.id!,
            start: c.start_ms / 1000,
            end: (c.start_ms + ('duration_ms' in c ? c.duration_ms : c.end_ms - c.start_ms)) / 1000,
            effectId: kind === 'subtitles' ? 'subtitle' : 'audio',
            selected: c.id === selectedAux,
            movable: !disabled && !track(kind).locked,
            flexible: !disabled && !track(kind).locked,
          }),
        ),
      })),
    ],
    [clips, sound, selected, selectedAux, disabled, revision, hideEmpty],
  );
  useEffect(() => {
    timeline.current?.setTime(frame / FPS);
  }, [frame]);
  function setFlag(kind: SoundTrack['id'], field: 'muted' | 'locked') {
    const value = { ...track(kind), [field]: !track(kind)[field] };
    onSoundChange({
      ...sound,
      tracks: [...sound.tracks.filter((t) => t.id !== kind), value],
    });
  }
  function begin(id: string, start: number, end: number) {
    onPause();
    origin.current = { id, start, end };
    setFeedback('');
    if (entries.some((e) => e.clip.id === id)) onSelect(id);
    else onSelectAux(id);
  }
  function auxiliary(id: string, start: number, end?: number, commit = true) {
    const item = allAux.find((c) => c.id === id),
      before = origin.current;
    if (!item || !before) return false;
    if (disabled || track('kind' in item ? item.kind : 'subtitles').locked) return false;
    const a = Math.max(0, snapMs(start * 1000));
    const length = 'duration_ms' in item ? item.duration_ms : item.end_ms - item.start_ms;
    const b = end === undefined ? a + length : snapMs(end * 1000);
    if (b <= a || b > 3600000) return false;
    const anchors = item.anchor_clip_id ? anchorAt(clips, a) : {};
    if ('duration_ms' in item) {
      const from =
        end === undefined || item.loop ? item.trim_in_ms : item.trim_in_ms + a - Math.round(before.start * 1000);
      const to = item.loop ? item.trim_out_ms : from + b - a;
      const duration =
        media[item.media_id]?.duration_ms ??
        clips.find((c) => c.media_id === item.media_id)?.duration_ms ??
        item.trim_out_ms;
      if (from < 0 || to > duration || b - a < 1) {
        setFeedback('已到声音素材边界');
        return false;
      }
      if (commit)
        onSoundChange({
          ...sound,
          audio_clips: sound.audio_clips.map((c) =>
            c.id === id
              ? {
                  ...c,
                  start_ms: a,
                  duration_ms: b - a,
                  ...(item.loop && end !== undefined
                    ? {
                        loop_offset_ms:
                          ((((item.loop_offset_ms ?? 0) + a - Math.round(before.start * 1000)) %
                            (item.trim_out_ms - item.trim_in_ms)) +
                            (item.trim_out_ms - item.trim_in_ms)) %
                          (item.trim_out_ms - item.trim_in_ms),
                      }
                    : {}),
                  trim_in_ms: from,
                  trim_out_ms: to,
                  ...anchors,
                }
              : c,
          ),
        });
    } else {
      if (sound.subtitles.some((c) => c.id !== id && a < c.end_ms && b > c.start_ms)) {
        setFeedback('字幕不能重叠，请调整到空闲区间');
        return false;
      }
      if (commit)
        onSoundChange({
          ...sound,
          subtitles: sound.subtitles
            .map((c) => (c.id === id ? { ...c, start_ms: a, end_ms: b, ...anchors } : c))
            .sort((x, y) => x.start_ms - y.start_ms),
        });
    }
    setFeedback(`${framecode(Math.round((a * FPS) / 1000))} → ${framecode(Math.round((b * FPS) / 1000))}`);
    return true;
  }
  function resize(id: string, start: number, end: number, dir: 'left' | 'right', commit: boolean) {
    const entry = entries.find((e) => e.clip.id === id),
      before = origin.current;
    if (!entry || !before) return auxiliary(id, start, end, commit);
    if (disabled || sound.picture_locked) return false;
    const a = dir === 'left' ? entry.clip.trim_in_ms + (start - before.start) * 1000 : entry.clip.trim_in_ms;
    const b =
      dir === 'right'
        ? (entry.clip.trim_out_ms ?? entry.clip.duration_ms ?? 0) + (end - before.end) * 1000
        : (entry.clip.trim_out_ms ?? entry.clip.duration_ms ?? 0);
    if (a < 0 || b > (entry.clip.duration_ms ?? 0) || b - a < 1000 / FPS) return false;
    if (commit) onChange(trimClip(clips, id, a, b));
    return true;
  }
  return (
    <section id={id} className="assembly-multitrack" aria-label="音画分轨时间轴">
      <div className="assembly-multitrack-heading">
        <span>
          {framecode(total)} <small>30 fps</small>
        </span>
        <div>
          <AssemblyAction
            label={hideEmpty ? '显示空轨道' : '隐藏空轨道'}
            icon={<Layers size={15} />}
            aria-pressed={hideEmpty}
            onClick={() => setHideEmpty(!hideEmpty)}
          />
          <AssemblyAction
            label={snapping ? '关闭吸附' : '开启吸附'}
            icon={<Magnet size={15} />}
            aria-pressed={snapping}
            onClick={() => setSnapping(!snapping)}
          />
          <AssemblyAction
            label="适应全部轨道"
            icon={<Maximize2 size={15} />}
            onClick={() => {
              setZoom(
                Math.max(4, Math.min(500, ((viewport.current?.clientWidth ?? 600) - 150) / Math.max(1, total / FPS))),
              );
              timeline.current?.setScrollLeft(0);
            }}
          />
          <AssemblyAction
            label="缩小轨道"
            icon={<Minus size={15} />}
            onClick={() => setZoom(Math.max(4, zoom / 1.5))}
          />
          <Slider aria-label="分轨时间轴缩放" min={4} max={500} value={zoom} onChange={setZoom} />
          <AssemblyAction
            label="放大轨道"
            icon={<Plus size={15} />}
            onClick={() => setZoom(Math.min(500, zoom * 1.5))}
          />
        </div>
      </div>
      <div
        className="assembly-multitrack-canvas"
        ref={viewport}
        onDragOver={(event) => {
          if (
            !disabled &&
            !sound.picture_locked &&
            event.dataTransfer.types.includes('application/x-assembly-source')
          ) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
          }
        }}
        onDrop={(event) => {
          const source = event.dataTransfer.getData('application/x-assembly-source');
          if (!source || disabled || sound.picture_locked) return;
          event.preventDefault();
          onPause();
          const at = Math.max(
            0,
            ((event.clientX - (viewport.current?.getBoundingClientRect().left ?? 0) - 128 + scroll.left) / zoom) * FPS,
          );
          const next = entries.find((e) => at < (e.start + e.end) / 2);
          onAdd(source, next ? clips.findIndex((c) => c.id === next.clip.id) : clips.length);
        }}
      >
        <Timeline
          ref={timeline}
          editorData={rows}
          effects={{
            video: { id: 'video', name: '画面' },
            audio: { id: 'audio', name: '声音' },
            subtitle: { id: 'subtitle', name: '字幕' },
          }}
          style={{ width: '100%', height: Math.max(112, height - 36) }}
          rowHeight={44}
          startLeft={128}
          scale={zoom < 20 ? 5 : 1}
          scaleWidth={zoom * (zoom < 20 ? 5 : 1)}
          scaleSplitCount={5}
          minScaleCount={Math.max(
            3,
            Math.ceil(
              Math.max(
                total / FPS,
                ...allAux.map(
                  (c) => (c.start_ms + ('duration_ms' in c ? c.duration_ms : c.end_ms - c.start_ms)) / 1000,
                ),
              ) / (zoom < 20 ? 5 : 1),
            ) + 2,
          )}
          autoScroll
          dragLine={snapping}
          disableDrag={disabled}
          onChange={() => false}
            onScroll={({ scrollLeft, scrollTop }) => {
              // 时间轴重排可能重复报告同一位置，复用状态以免播放游标触发更新循环。
              setScroll((previous) =>
                previous.left === scrollLeft && previous.top === scrollTop
                  ? previous
                  : { left: scrollLeft, top: scrollTop },
              );
            }}
          onClickTimeArea={(time) => {
            onPause();
            onSeek(Math.round(time * FPS));
            return false;
          }}
          onCursorDragStart={onPause}
          onCursorDrag={(time) => onSeek(Math.round(time * FPS))}
          onCursorDragEnd={(time) => onSeek(Math.round(time * FPS))}
          onClickActionOnly={(_, { action, time }) => {
            if (entries.some((e) => e.clip.id === action.id)) onSelect(action.id);
            else onSelectAux(action.id);
            onPause();
            onSeek(Math.round(time * FPS));
          }}
          onActionMoveStart={({ action }) => begin(action.id, action.start, action.end)}
          onActionMoving={({ action, start }) =>
            entries.some((e) => e.clip.id === action.id)
              ? !disabled && !sound.picture_locked
              : auxiliary(action.id, start, undefined, false)
          }
          onActionMoveEnd={({ action, start }) => {
            if (entries.some((e) => e.clip.id === action.id)) {
              if (sound.picture_locked || disabled) return;
              const others = entries.filter((e) => e.clip.id !== action.id),
                target = others.find((e) => start * FPS < (e.start + e.end) / 2);
              onChange(
                moveClip(
                  clips,
                  action.id,
                  target
                    ? clips.filter((c) => c.id !== action.id).findIndex((c) => c.id === target.clip.id)
                    : clips.length - 1,
                ),
              );
            } else auxiliary(action.id, start);
            setRevision((v) => v + 1);
          }}
          onActionResizeStart={({ action }) => begin(action.id, action.start, action.end)}
          onActionResizing={({ action, start, end, dir }) => resize(action.id, start, end, dir, false)}
          onActionResizeEnd={({ action, start, end, dir }) => {
            resize(action.id, start, end, dir, true);
            setRevision((v) => v + 1);
          }}
          getScaleRender={(time) => <span>{framecode(Math.round(time * FPS)).slice(0, -3)}</span>}
          getActionRender={(action) => {
            const entry = entries.find((e) => e.clip.id === action.id),
              item = allAux.find((c) => c.id === action.id);
            return (
              <button
                className={`assembly-lane-clip${action.id === selectedAux || (!selectedAux && action.id === selected) ? ' is-selected' : ''}${item && 'muted' in item && item.muted ? ' is-muted' : ''}`}
                aria-label={
                  entry
                    ? `画面 ${entry.clip.script || entry.clip.shot_position}`
                    : item && 'name' in item
                      ? item.name
                      : `字幕 ${item?.text}`
                }
                onFocus={() => (entry ? onSelect(action.id) : onSelectAux(action.id))}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    event.stopPropagation();
                    onPause();
                    onSeek(Math.round(action.start * FPS));
                  }
                  if (event.altKey && !disabled && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
                    event.preventDefault();
                    event.stopPropagation();
                    begin(action.id, action.start, action.end);
                    if (entry && !sound.picture_locked)
                      onChange(
                        moveClip(
                          clips,
                          action.id,
                          clips.findIndex((c) => c.id === action.id) + (event.key === 'ArrowLeft' ? -1 : 1),
                        ),
                      );
                    else auxiliary(action.id, action.start + (event.key === 'ArrowLeft' ? -1 : 1) / FPS);
                  }
                }}
              >
                {entry ? (
                  <AssemblyFilmstrip
                    clip={entry.clip}
                    start={128 + (entry.start / FPS) * zoom}
                    width={((entry.end - entry.start) / FPS) * zoom}
                    height={38}
                    viewport={{
                      left: scroll.left,
                      width: viewport.current?.clientWidth ?? 800,
                    }}
                  />
                ) : item && 'media_id' in item ? (
                  <AudioWaveform
                    values={waveforms[item.media_id]}
                    clip={item}
                    duration={media[item.media_id]?.duration_ms}
                  />
                ) : (
                  <Captions size={13} />
                )}
                <span>
                  {entry
                    ? entry.clip.script || `镜头 ${entry.clip.shot_position}`
                    : item && 'name' in item
                      ? item.name
                      : item?.text}
                </span>
              </button>
            );
          }}
        />
        <div className="assembly-track-labels" aria-label="轨道控制" style={{ top: 32 - scroll.top }}>
          <div>
            <span>画面</span>
            <AssemblyAction
              label={sound.picture_locked ? '解锁画面轨' : '锁定画面轨'}
              icon={sound.picture_locked ? <LockKeyhole size={12} /> : <UnlockKeyhole size={12} />}
              disabled={disabled}
              onClick={() =>
                onSoundChange({
                  ...sound,
                  picture_locked: !sound.picture_locked,
                })
              }
            />
          </div>
          {visibleKinds.map((kind) => (
            <div key={kind}>
              <span>{names[kind]}</span>
              <AssemblyAction
                label={`${track(kind).muted ? '恢复' : '静音'}${names[kind]}轨`}
                icon={track(kind).muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
                disabled={disabled}
                onClick={() => setFlag(kind, 'muted')}
              />
              <AssemblyAction
                label={`${track(kind).locked ? '解锁' : '锁定'}${names[kind]}轨`}
                icon={track(kind).locked ? <LockKeyhole size={12} /> : <UnlockKeyhole size={12} />}
                disabled={disabled}
                onClick={() => setFlag(kind, 'locked')}
              />
            </div>
          ))}
        </div>
        {feedback && (
          <output className="assembly-track-feedback" aria-live={playing ? 'off' : 'polite'}>
            {feedback}
          </output>
        )}
      </div>
    </section>
  );
}
