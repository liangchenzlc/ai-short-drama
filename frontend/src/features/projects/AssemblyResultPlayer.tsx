import { aspectRatio } from './assembly-document';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { Button, Tooltip } from 'antd';
import { Download, Info, RefreshCw } from 'lucide-react';
import { AssemblyAction } from './AssemblyAction';
import { AssemblyPlaybackControls } from './AssemblyPlaybackControls';
import type { AssemblyCurrentWork, RenderJob } from '../../api/modules/assembly';
import { FPS } from './assembly-editing';

type Phase = 'loading' | 'ready' | 'playing' | 'buffering' | 'error' | 'refreshing' | 'restoring';
type Playback = { time: number; volume: number; muted: boolean; rate: number };

export function AssemblyResultPlayer<T extends RenderJob | AssemblyCurrentWork>({ job, aspect, stale, videoRef, refresh, onResult, onFrame, onPlaying, download }: {
  job: T; aspect: string; stale: boolean; videoRef: RefObject<HTMLVideoElement | null>;
  refresh: (signal?: AbortSignal) => Promise<T>; onResult: (job: T) => void;
  onFrame: (frame: number) => void; onPlaying: (value: boolean) => void; download: string;
}) {
  const initial = job.url ? 'loading' : 'error';
  const [phase, setPhase] = useState<Phase>(initial);
  const [position, setPosition] = useState(0), [muted, setMuted] = useState(false);
  const surface = useRef<HTMLElement>(null);
  const phaseRef = useRef<Phase>(initial);
  const [error, setError] = useState(job.url ? '' : '成片地址不可用，请刷新后重试。');
  // Explicit retries replace the decoder, isolating old events. Layout changes do not.
  const [source, setSource] = useState({ url: job.url, attempt: 0 });
  const attempt = useRef(0), mounted = useRef(false);
  const request = useRef<AbortController | null>(null);
  const saved = useRef<Playback>({ time: 0, volume: 1, muted: false, rate: 1 });
  const target = useRef<number | null>(null);
  const callbacks = useRef({ onPlaying, onFrame }); callbacks.current = { onPlaying, onFrame };
  function transition(next: Phase) { phaseRef.current = next; setPhase(next); }
  function fail(message: string) {
    request.current?.abort(); request.current = null;
    transition('error'); setError(message);
    videoRef.current?.pause(); callbacks.current.onPlaying(false);
  }
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; request.current?.abort(); };
  }, []);
  const busy = phase === 'refreshing' || phase === 'restoring';
  const waiting = busy || phase === 'loading' || phase === 'buffering';
  useEffect(() => {
    if (!waiting) return;
    const timer = setTimeout(() => fail('成片加载超时，播放位置已保留。请检查网络后重试。'), 20000);
    return () => clearTimeout(timer);
  }, [waiting, source.attempt]);
  const current = (video: HTMLVideoElement) => mounted.current && video === videoRef.current && source.attempt === attempt.current;
  function remember(video: HTMLVideoElement) {
    if (!current(video) || video.error || target.current !== null || ['error', 'refreshing', 'restoring'].includes(phaseRef.current)) return;
    if (video.readyState >= 1 && Number.isFinite(video.currentTime)) saved.current.time = video.currentTime;
    setPosition(Math.round(saved.current.time * FPS)); callbacks.current.onFrame(Math.round(saved.current.time * FPS));
  }
  function ready(video: HTMLVideoElement) {
    if (!current(video) || ['error', 'refreshing'].includes(phaseRef.current) || video.error || video.readyState < 2 || video.seeking) return;
    if (target.current !== null) {
      if (Math.abs(video.currentTime - target.current) > 1 / FPS) return;
      saved.current.time = target.current; target.current = null;
      video.pause(); setPosition(Math.round(saved.current.time * FPS)); callbacks.current.onFrame(Math.round(saved.current.time * FPS));
    }
    setError(''); transition(video.paused ? 'ready' : 'playing');
  }
  async function retry() {
    if (['refreshing', 'restoring'].includes(phaseRef.current)) return;
    const video = videoRef.current;
    if (video) remember(video);
    target.current = target.current ?? saved.current.time;
    transition('refreshing'); video?.pause(); callbacks.current.onPlaying(false);
    const revision = ++attempt.current;
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    try {
      const fresh = await refresh(controller.signal);
      if (!mounted.current || revision !== attempt.current || controller.signal.aborted) return;
      if (fresh.media_id !== job.media_id || 'id' in job && (!('id' in fresh) || fresh.id !== job.id) || 'status' in fresh && fresh.status !== 'succeeded' || !fresh.url) {
        fail('原成片暂不可用，无法恢复播放。请核对任务结果，或尝试下载 MP4。'); return;
      }
      onResult(fresh); transition('restoring');
      setSource({ url: fresh.url, attempt: revision });
    } catch {
      if (mounted.current && revision === attempt.current && !controller.signal.aborted) fail('刷新成片失败，播放位置已保留。请检查网络后重试。');
    } finally { if (request.current === controller) request.current = null; }
  }
  return <section className="assembly-player assembly-result-player" ref={surface} aria-label="实际成片播放" data-playback-state={phase}>
    <div style={{ aspectRatio: aspectRatio(aspect) }} className={`assembly-screen assembly-result-screen${aspectRatio(aspect) < 1 ? ' is-portrait' : ''}`}>
      <video className="is-active" key={source.attempt} ref={videoRef} src={source.url ?? undefined} playsInline preload="auto"
        onTimeUpdate={event => remember(event.currentTarget)} onSeeking={event => remember(event.currentTarget)}
        onVolumeChange={event => { const v = event.currentTarget; if (current(v) && v.readyState >= 1 && ['ready', 'playing', 'buffering'].includes(phaseRef.current)) { saved.current.volume = v.volume; saved.current.muted = v.muted; setMuted(v.muted); } }}
        onRateChange={event => { const v = event.currentTarget; if (current(v) && v.readyState >= 1 && ['ready', 'playing', 'buffering'].includes(phaseRef.current)) saved.current.rate = v.playbackRate; }}
        onPlay={event => {
          if (!current(event.currentTarget) || ['error', 'refreshing', 'restoring'].includes(phaseRef.current)) { event.currentTarget.pause(); return; }
          transition('playing'); callbacks.current.onPlaying(true);
        }}
        onEnded={() => { transition('ready'); callbacks.current.onPlaying(false); }}
        onPause={event => { if (current(event.currentTarget)) { callbacks.current.onPlaying(false); if (phaseRef.current === 'playing') transition('ready'); } }}
        onLoadedMetadata={event => {
          const video = event.currentTarget;
          if (!current(video) || phaseRef.current === 'error') return;
          const settings = { ...saved.current };
          video.volume = settings.volume; video.muted = settings.muted; video.playbackRate = settings.rate;
          if (target.current !== null && Number.isFinite(video.duration)) {
            target.current = Math.min(target.current, video.duration);
            video.currentTime = target.current;
          }
          ready(video);
        }}
        onCanPlay={event => ready(event.currentTarget)} onSeeked={event => ready(event.currentTarget)}
        onPlaying={event => ready(event.currentTarget)}
        onWaiting={event => { if (current(event.currentTarget) && ['ready', 'playing'].includes(phaseRef.current)) transition('buffering'); }}
        onError={event => {
          if (current(event.currentTarget)) fail(event.currentTarget.error?.code === 4
            ? '成片格式不可播放或地址已失效，请刷新地址重试，也可下载 MP4。'
            : '成片播放中断，请刷新成片地址后重试。');
        }}/>
      {waiting && !error && <div className="assembly-player-message" role="status">{busy ? '正在恢复播放位置…' : '正在加载成片…'}</div>}
      {error && <div className="assembly-player-message" role="alert"><p>{error}</p><Button aria-label="刷新成片并重试" loading={busy} onClick={() => { void retry(); }}>刷新成片并重试</Button></div>}
    </div>
    <AssemblyPlaybackControls playing={phase === 'playing' || phase === 'buffering'} disabled={waiting || !!error} position={position} total={Math.round((job.duration_ms ?? 0) / 1000 * FPS)} muted={muted}
      onToggle={() => { const video = videoRef.current; if (!video) return; if (!video.paused) video.pause(); else void video.play().catch(() => fail('无法开始播放，请刷新成片地址后重试。')); }}
      onStep={delta => { const video = videoRef.current; if (video && Number.isFinite(video.duration)) { video.pause(); video.currentTime = Math.max(0, Math.min(video.duration, (position + delta) / FPS)); } }}
      onMute={() => { const video = videoRef.current; if (video) video.muted = !video.muted; }}
      onFullscreen={() => { const request = document.fullscreenElement ? document.exitFullscreen() : surface.current?.requestFullscreen(); void request?.catch(() => fail('当前浏览器不支持全屏播放。')); }}>
      <Tooltip title={`${'kind' in job ? job.kind === 'preview' ? '合成预览' : '实际导出成片' : '当前采用成片'}：${stale ? '草稿已有修改，此处显示导出时的版本' : '当前保存版本'}`} trigger={['hover', 'focus']}><Button type="text" size="small" className="assembly-icon-action" icon={<Info size={16}/>} aria-label="成片版本信息"/></Tooltip>
      <AssemblyAction label="刷新成片地址" icon={<RefreshCw size={16}/>} loading={busy} onClick={() => { void retry(); }}/>
      <AssemblyAction label="下载 MP4" icon={<Download size={16}/>} href={download}/>
    </AssemblyPlaybackControls>
  </section>;
}
