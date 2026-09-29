import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Button } from 'antd';
import type { AssemblyClip } from '../../api/modules/assembly';
import { buildTimeline, FPS, framecode, locateFrame, toFrame, type TimelineEntry } from './assembly-editing';

export interface AssemblyPlayerHandle {
  seek: (frame: number) => void;
  pause: () => void;
  toggle: () => void;
}

function ready(video: HTMLVideoElement, signal: AbortSignal, test: () => boolean) {
  return new Promise<void>((resolve, reject) => {
    const events = ['loadedmetadata', 'loadeddata', 'canplay', 'seeked', 'error'];
    const clean = () => { clearTimeout(timeout); events.forEach(e => video.removeEventListener(e, check)); signal.removeEventListener('abort', abort); };
    const abort = () => { clean(); reject(new DOMException('Cancelled', 'AbortError')); };
    const check = () => {
      if (video.error) { clean(); reject(new Error('视频加载失败，请刷新播放地址后重试。')); }
      else if (test()) { clean(); resolve(); }
    };
    const timeout = setTimeout(() => { clean(); reject(new Error('视频加载超时，请检查网络并重试。')); }, 20000);
    events.forEach(e => video.addEventListener(e, check)); signal.addEventListener('abort', abort);
    if (signal.aborted) abort(); else check();
  });
}

/** Two bounded decoders share one timeline clock; only the visible decoder has audio. */
export const AssemblyPlayer = forwardRef<AssemblyPlayerHandle, {
  clips: AssemblyClip[]; aspect: string; onFrame: (frame: number) => void;
  onPlaying: (playing: boolean) => void; onRefresh: () => Promise<void>;
}>(function AssemblyPlayer({ clips, aspect, onFrame, onPlaying, onRefresh }, ref) {
  const videos = useRef<(HTMLVideoElement | null)[]>([]);
  const surface = useRef<HTMLElement>(null);
  const controllers = useRef<(AbortController | null)[]>([null, null]);
  const active = useRef<{ slot: number; id: string } | null>(null);
  const sequence = useRef(0), frame = useRef(0), playing = useRef(false), switching = useRef(false);
  const callbacks = useRef({ onFrame, onPlaying }); callbacks.current = { onFrame, onPlaying };
  const entries = useRef<TimelineEntry[]>([]); entries.current = buildTimeline(clips);
  const [slot, setSlot] = useState(0), [buffering, setBuffering] = useState(false);
  const [error, setError] = useState(''), [isPlaying, setIsPlaying] = useState(false);
  const [retrying, setRetrying] = useState(false), [retryEpoch, setRetryEpoch] = useState(0);
  const refreshPending = useRef(false);
  const [position, setPosition] = useState(0);
  const total = entries.current.at(-1)?.end ?? 0;
  const update = (at: number) => { frame.current = at; setPosition(at); callbacks.current.onFrame(at); };
  function setPlayback(value: boolean) {
    playing.current = value; setIsPlaying(value); callbacks.current.onPlaying(value);
  }
  function pause() {
    ++sequence.current; switching.current = false;
    controllers.current.forEach(c => c?.abort()); setBuffering(false);
    setPlayback(false); videos.current.forEach(v => v?.pause());
  }
  async function prepare(index: number, entry: TimelineEntry, at: number) {
    controllers.current[index]?.abort();
    const controller = new AbortController(); controllers.current[index] = controller;
    const video = videos.current[index]!;
    video.pause(); video.muted = true;
    const url = entry.clip.url;
    if (!url || entry.clip.issue) throw new Error('这个片段尚不可播放，请补齐视频或修正裁剪范围。');
    const mediaId = entry.clip.media_id ?? entry.clip.id;
    // Signed URLs rotate during polling. Keep a prepared decoder for the same
    // immutable media rather than reloading it at every cut.
    if (video.dataset.mediaId !== mediaId || video.error) {
      video.dataset.mediaId = mediaId; video.src = url; video.load();
    }
    await ready(video, controller.signal, () => video.readyState >= 1);
    const sourceTime = (toFrame(entry.clip.trim_in_ms) + at - entry.start) / FPS;
    if (Math.abs(video.currentTime - sourceTime) > .001) video.currentTime = sourceTime;
    await ready(video, controller.signal, () => video.readyState >= 2 && !video.seeking);
    return video;
  }
  async function seek(at: number, resume = playing.current) {
    const generation = ++sequence.current;
    switching.current = true;
    videos.current.forEach(v => v?.pause());
    controllers.current.forEach(c => c?.abort());
    const end = entries.current.at(-1)?.end ?? 0;
    const target = Math.max(0, Math.min(Math.round(at), end));
    const entry = locateFrame(entries.current, target);
    if (!entry) { switching.current = false; pause(); update(0); setBuffering(false); return; }
    const index = active.current?.id === entry.clip.id ? active.current.slot : 1 - (active.current?.slot ?? 1);
    // Record intent immediately: rapid stepping and retries use the requested
    // position, even if the previous seek has not decoded a frame yet.
    update(target); setPlayback(resume); setError(''); setBuffering(true);
    try {
      const video = await prepare(index, entry, Math.min(target, end - 1));
      if (generation !== sequence.current) return;
      active.current = { slot: index, id: entry.clip.id };
      setSlot(index);
      videos.current.forEach(v => { if (v) v.muted = true; });
      video.muted = entry.clip.muted;
      if (resume) await video.play();
      if (generation !== sequence.current) return;
      setBuffering(false); switching.current = false;
      const next = entries.current[entries.current.indexOf(entry) + 1];
      if (next && !next.clip.issue && next.clip.url) void prepare(1 - index, next, next.start).catch(() => {});
    } catch (cause) {
      if (generation !== sequence.current || (cause instanceof DOMException && cause.name === 'AbortError')) return;
      switching.current = false; setBuffering(false); pause(); setError((cause as Error).message);
    }
  }
  function toggle() {
    if (playing.current) pause();
    else void seek(frame.current >= total - 1 ? 0 : frame.current, true);
  }
  useImperativeHandle(ref, () => ({ seek: at => { pause(); void seek(at, false); }, pause, toggle }));
  const actions = useRef({ seek, pause }); actions.current = { seek, pause };
  async function retry() {
    if (refreshPending.current) return;
    refreshPending.current = true; setRetrying(true); pause();
    const generation = sequence.current;
    try {
      await onRefresh();
      if (generation !== sequence.current) { setRetrying(false); return; }
      videos.current.forEach(v => v?.removeAttribute('data-media-id'));
      // The effect runs after React commits refreshed URLs, including when the
      // media ID has not changed and the previous URL merely expired.
      setRetryEpoch(value => value + 1);
    } catch {
      if (generation === sequence.current) setError('刷新视频失败，剪辑已保留。请检查网络后重试。');
      setRetrying(false);
    } finally { refreshPending.current = false; }
  }
  useEffect(() => {
    if (retryEpoch) void actions.current.seek(frame.current, false).finally(() => setRetrying(false));
  }, [retryEpoch]);
  // Media URL refreshes do not interrupt playback. Actual edit decisions do.
  const signature = JSON.stringify(clips.map(c => [c.id, c.media_id, c.trim_in_ms, c.trim_out_ms, c.duration_ms, c.included, c.muted, c.issue]));
  useEffect(() => { actions.current.pause(); void actions.current.seek(frame.current, false); }, [signature]);
  useEffect(() => {
    if (!buffering) return;
    const timeout = setTimeout(() => {
      actions.current.pause(); setError('视频缓冲超时，已保留播放位置。请检查网络后重试。');
    }, 20000);
    return () => clearTimeout(timeout);
  }, [buffering]);
  useEffect(() => {
    let animation = 0;
    const tick = () => {
      const current = active.current;
      const video = current ? videos.current[current.slot] : null;
      const entry = current ? entries.current.find(e => e.clip.id === current.id) : undefined;
      if (playing.current && !switching.current && video && entry && !video.seeking) {
        const relative = Math.floor(video.currentTime * FPS + .001) - toFrame(entry.clip.trim_in_ms);
        const at = Math.max(entry.start, entry.start + relative);
        if (at >= entry.end || video.ended) {
          const next = entries.current[entries.current.indexOf(entry) + 1];
          if (next) void actions.current.seek(next.start, true);
          else { actions.current.pause(); update(entry.end); }
        } else if (at !== frame.current) update(at);
      }
      animation = requestAnimationFrame(tick);
    };
    animation = requestAnimationFrame(tick);
    // Browsers throttle animation frames in background tabs; pause rather than
    // letting a hidden decoder play beyond a trimmed out-point.
    const hide = () => { if (document.hidden && playing.current) actions.current.pause(); };
    document.addEventListener('visibilitychange', hide);
    return () => { document.removeEventListener('visibilitychange', hide); cancelAnimationFrame(animation); ++sequence.current; controllers.current.forEach(c => c?.abort()); videos.current.forEach(v => v?.pause()); };
  }, []);
  return <section className="assembly-player" aria-label="时间轴成片预览" ref={surface}>
    <div className={`assembly-screen${aspect === '9:16' ? ' is-portrait' : ''}`}>
      {[0, 1].map(index => <video key={index} ref={element => { videos.current[index] = element; }}
        className={slot === index && total ? 'is-active' : ''} playsInline preload="auto"
        aria-hidden={slot !== index} onWaiting={() => { if (active.current?.slot === index) setBuffering(true); }}
        onError={() => { if (active.current?.slot === index && !switching.current) { pause(); setBuffering(false); setError('视频播放中断，请刷新播放地址后重试。'); } }}
        onCanPlay={() => { if (active.current?.slot === index && !switching.current) setBuffering(false); }}
        onPlaying={() => { if (active.current?.slot === index) setBuffering(false); }} />)}
      {!total && <div className="assembly-no-video"><strong>从分镜开始剪辑</strong><p>将分镜视频添加到下方轨道，在这里观看完整故事。</p></div>}
      {!!total && buffering && !error && <div className="assembly-player-message" role="status">正在准备画面…</div>}
      {error && <div className="assembly-player-message" role="alert"><p>{error}</p><Button loading={retrying} onClick={() => { void retry(); }}>刷新视频并重试</Button></div>}
    </div>
    <div className="assembly-player-controls">
      <Button disabled={!total} onClick={() => { pause(); void seek(frame.current - 1, false); }} aria-label="上一帧">上一帧</Button>
      <Button type="primary" disabled={!total} onClick={toggle}>{isPlaying ? '暂停' : '播放'}</Button>
      <Button disabled={!total} onClick={() => { pause(); void seek(frame.current + 1, false); }} aria-label="下一帧">下一帧</Button>
      <output aria-label="播放位置">{framecode(position)} <span>/ {framecode(total)}</span></output>
      <Button type="text" onClick={() => { const request = document.fullscreenElement ? document.exitFullscreen() : surface.current?.requestFullscreen(); void request?.catch(() => setError('当前浏览器不支持全屏播放。')); }}>全屏</Button>
    </div>
  </section>;
});
