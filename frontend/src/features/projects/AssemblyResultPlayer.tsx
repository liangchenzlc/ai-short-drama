import { useEffect, useRef, useState, type RefObject } from 'react';
import { Button } from 'antd';
import type { RenderJob } from '../../api/modules/assembly';
import { FPS } from './assembly-editing';

export function AssemblyResultPlayer({ job, aspect, stale, videoRef, refresh, onResult, onFrame, onPlaying, download }: {
  job: RenderJob; aspect: string; stale: boolean; videoRef: RefObject<HTMLVideoElement | null>;
  refresh: () => Promise<RenderJob>; onResult: (job: RenderJob) => void;
  onFrame: (frame: number) => void; onPlaying: (value: boolean) => void; download: string;
}) {
  const [buffering, setBuffering] = useState(!!job.url), [busy, setBusy] = useState(false);
  const [error, setError] = useState(job.url ? '' : '成片地址不可用，请刷新后重试。');
  const [revision, setRevision] = useState(0);
  const restoring = useRef<number | null>(null), pending = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!revision) return;
    videoRef.current?.load();
  }, [revision, videoRef]);
  useEffect(() => {
    if (!buffering) return;
    const timeout = setTimeout(() => {
      videoRef.current?.pause(); setBuffering(false);
      setError('成片加载超时，播放位置已保留。请检查网络后重试。');
    }, 20000);
    return () => clearTimeout(timeout);
  }, [buffering, videoRef]);
  async function retry() {
    if (pending.current) return;
    pending.current = true; setBusy(true);
    restoring.current = restoring.current ?? videoRef.current?.currentTime ?? 0;
    videoRef.current?.pause();
    try {
      const fresh = await refresh();
      if (!mounted.current) return;
      if (!fresh.url) throw new Error('missing_url');
      onResult(fresh); setError(''); setBuffering(true); setRevision(value => value + 1);
    } catch {
      if (mounted.current) setError('刷新成片失败，播放位置已保留。请检查网络后重试。');
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return <section aria-label="实际成片播放">
    <div className={`assembly-screen assembly-result-screen${aspect === '9:16' ? ' is-portrait' : ''}`}>
      <video ref={videoRef} src={job.url ?? undefined} controls playsInline
        onTimeUpdate={event => onFrame(Math.round(event.currentTarget.currentTime * FPS))}
        onPlay={() => onPlaying(true)} onPause={() => onPlaying(false)}
        onLoadedMetadata={event => {
          if (restoring.current !== null) {
            const video = event.currentTarget;
            video.currentTime = Math.min(restoring.current, Number.isFinite(video.duration) ? video.duration : restoring.current);
            restoring.current = null;
          }
        }}
        onCanPlay={() => { setBuffering(false); setError(''); }}
        onPlaying={() => setBuffering(false)} onWaiting={() => setBuffering(true)}
        onError={() => { setBuffering(false); onPlaying(false); setError('成片播放中断，请刷新成片地址后重试。'); }}/>
      {buffering && !error && <div className="assembly-player-message" role="status">正在加载成片…</div>}
      {error && <div className="assembly-player-message" role="alert"><p>{error}</p><Button loading={busy} onClick={() => { void retry(); }}>刷新成片并重试</Button></div>}
    </div>
    <div className="assembly-result-caption"><span>{job.kind === 'preview' ? '已合成预览' : '实际导出成片'}{stale ? ' · 草稿已有修改，此处显示导出时的版本' : ' · 当前保存版本'}</span>
      <Button type="link" loading={busy} onClick={() => { void retry(); }}>刷新成片地址</Button><a href={download}>下载 MP4</a></div>
  </section>;
}
