import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import {
  audibleAudio,
  audioGain,
  audioSourceTime,
  frameGeometry,
} from '@host/features/creation/assembly/player/assembly-playback';
import { AssemblyPlaybackControls } from '@host/features/creation/assembly/player/AssemblyPlaybackControls';
import { aspectRatio, normalizeSound } from '@host/features/creation/assembly/sessions/assembly-document';
import { buildTimeline, FPS, locateFrame, toMs } from '@host/features/creation/assembly/sessions/assembly-editing';
import type { AssemblyClip, FrameTransform } from '@host/types/creation/assembly/document';
import type { Voice } from '@host/types/creation/assembly/player/AssemblyEditorPlayer';
import type { AssemblyPlayerHandle } from '@host/types/creation/assembly/player/AssemblyPlayer';
import type { SoundState } from '@host/types/creation/sound/tracks';
import { Button } from 'antd';
import { Crop, Scan } from 'lucide-react';
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type CSSProperties } from 'react';
function mediaReady(media: HTMLMediaElement, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const events = ['canplay', 'seeked', 'loadeddata', 'error'];
    const clean = () => {
      clearTimeout(timer);
      events.forEach((e) => media.removeEventListener(e, check));
      signal.removeEventListener('abort', abort);
    };
    const abort = () => {
      clean();
      reject(new DOMException('Cancelled', 'AbortError'));
    };
    const check = () => {
      if (media.error) {
        clean();
        reject(new Error('媒体加载失败，请刷新地址后重试。'));
      } else if (media.readyState >= 2 && !media.seeking) {
        clean();
        resolve();
      }
    };
    const timer = setTimeout(() => {
      clean();
      reject(new Error('媒体缓冲超时，请检查网络后重试。'));
    }, 20000);
    events.forEach((e) => media.addEventListener(e, check));
    signal.addEventListener('abort', abort);
    if (signal.aborted) abort();
    else check();
  });
}

/** One audio clock drives pictures, every sound lane and captions, including stills. */
export const AssemblyEditorPlayer = forwardRef<
  AssemblyPlayerHandle,
  {
    clips: AssemblyClip[];
    sound: SoundState;
    aspect: string;
    font?: string;
    readOnly?: boolean;
    onFrame: (frame: number) => void;
    onPlaying: (playing: boolean) => void;
    onRefresh: () => Promise<void>;
    onTransform: (id: string, transform: FrameTransform) => void;
  }
>(function AssemblyEditorPlayer(props, ref) {
  const current = useRef(props);
  current.current = props;
  const surface = useRef<HTMLElement>(null),
    box = useRef<HTMLDivElement>(null),
    videos = useRef<(HTMLVideoElement | null)[]>([]);
  const context = useRef<AudioContext | null>(null),
    master = useRef<DynamicsCompressorNode | null>(null),
    voices = useRef(new Map<string, Voice>());
  const clock = useRef({ at: 0, origin: 0, playing: false, generation: 0 }),
    controller = useRef<AbortController | null>(null),
    active = useRef<{ id: string; slot: number } | null>(null);
  const [position, setPosition] = useState(0),
    [playing, setPlaying] = useState(false),
    [buffering, setBuffering] = useState(false),
    [error, setError] = useState('');
  const [slot, setSlot] = useState(0),
    [muted, setMuted] = useState(false),
    [safe, setSafe] = useState(false),
    [cropping, setCropping] = useState(false);
  const mutedRef = useRef(false),
    [size, setSize] = useState({ width: 640, height: 360 }),
    [retrying, setRetrying] = useState(false);
  const [draftTransform, setDraftTransform] = useState<FrameTransform | null>(null);
  const drag = useRef<{
    x: number;
    y: number;
    transform: FrameTransform;
  } | null>(null);
  const entries = buildTimeline(props.clips),
    total = entries.at(-1)?.end ?? 0,
    entry = locateFrame(entries, position);
  const sound = normalizeSound(props.sound.document, props.clips, props.sound.media);
  const transform = draftTransform ?? entry?.clip.transform;
  const ratio = aspectRatio(props.aspect);
  const canvasWidth = Math.min(size.width, size.height * ratio),
    canvasHeight = canvasWidth / ratio;
  const geometry = frameGeometry(
    transform,
    canvasWidth,
    canvasHeight,
    entry?.clip.width || canvasWidth,
    entry?.clip.height || canvasHeight,
  );
  const timeNow = () => context.current?.currentTime ?? performance.now() / 1000;
  function show(at: number) {
    clock.current.at = at;
    setPosition(at);
    current.current.onFrame(at);
  }
  function stop() {
    clock.current.generation++;
    controller.current?.abort();
    clock.current.playing = false;
    setPlaying(false);
    current.current.onPlaying(false);
    setBuffering(false);
    videos.current.forEach((v) => v?.pause());
    voices.current.forEach((v) => v.element.pause());
  }
  function clearVoices() {
    voices.current.forEach((v) => {
      v.element.pause();
      v.source.disconnect();
      v.gain.disconnect();
      v.element.removeAttribute('src');
      v.element.load();
    });
    voices.current.clear();
  }
  function audioContext() {
    if (!context.current) {
      const ctx = new AudioContext();
      context.current = ctx;
      const limiter = ctx.createDynamicsCompressor();
      limiter.threshold.value = -1;
      limiter.knee.value = 0;
      limiter.ratio.value = 20;
      limiter.attack.value = 0.003;
      limiter.release.value = 0.1;
      limiter.connect(ctx.destination);
      master.current = limiter;
    }
    return context.current;
  }
  function syncAudio(at: number, run: boolean) {
    const state = current.current,
      doc = normalizeSound(state.sound.document, state.clips, state.sound.media);
    const wanted = audibleAudio(doc).filter((c) => at >= c.start_ms && at < c.start_ms + c.duration_ms);
    const ids = new Set(wanted.map((c) => c.id));
    voices.current.forEach((voice, id) => {
      if (!ids.has(id)) {
        voice.element.pause();
        voice.source.disconnect();
        voice.gain.disconnect();
        voice.element.removeAttribute('src');
        voice.element.load();
        voices.current.delete(id);
      }
    });
    const promises: Promise<void>[] = [];
    for (const clip of wanted) {
      let voice = voices.current.get(clip.id);
      if (!voice) {
        const url = state.sound.media[clip.media_id]?.url ?? state.clips.find((c) => c.media_id === clip.media_id)?.url;
        if (!url) throw new Error(`“${clip.name}”缺少声音地址，请刷新素材。`);
        const ctx = audioContext(),
          element = new Audio();
        element.crossOrigin = 'anonymous';
        element.preload = 'auto';
        element.src = url;
        const source = ctx.createMediaElementSource(element),
          gain = ctx.createGain();
        source.connect(gain);
        gain.connect(master.current!);
        voice = { element, source, gain, mediaId: clip.media_id };
        voices.current.set(clip.id, voice);
      }
      const time = audioSourceTime(clip, at);
      if (Math.abs(voice.element.currentTime - time) > 0.12) voice.element.currentTime = time;
      voice.gain.gain.value = mutedRef.current ? 0 : audioGain(clip, at, doc);
      if (run && voice.element.paused) promises.push(voice.element.play());
    }
    return promises;
  }
  async function seek(at: number, resume = false) {
    stop();
    const generation = clock.current.generation;
    const abort = new AbortController();
    controller.current = abort;
    const rows = buildTimeline(current.current.clips),
      end = rows.at(-1)?.end ?? 0,
      target = Math.max(0, Math.min(end, Math.round(at))),
      targetEntry = locateFrame(rows, Math.min(target, Math.max(0, end - 1)));
    show(target);
    setError('');
    if (!targetEntry) return;
    setBuffering(true);
    try {
      if (resume) await audioContext().resume();
      const clip = targetEntry.clip;
      if (!clip.url || clip.issue) throw new Error('当前画面不可用，请替换素材或修正裁剪。');
      const index = active.current?.id === clip.id ? active.current.slot : 1 - (active.current?.slot ?? 1);
      const video = videos.current[index]!;
      if (clip.source_type === 'image') {
        const image = new Image();
        image.src = clip.url;
        await image.decode();
      } else {
        if (video.dataset.mediaId !== clip.media_id || video.error) {
          video.dataset.mediaId = clip.media_id!;
          video.src = clip.url;
          video.load();
        }
        await mediaReady(video, abort.signal);
        video.currentTime = Math.min(
          (clip.duration_ms ?? Infinity) / 1000 - 0.001,
          (clip.trim_in_ms + toMs(target - targetEntry.start)) / 1000,
        );
        await mediaReady(video, abort.signal);
      }
      if (generation !== clock.current.generation) return;
      active.current = { id: clip.id, slot: index };
      setSlot(index);
      if (resume) {
        syncAudio(toMs(target), false);
        await Promise.all([...voices.current.values()].map((v) => mediaReady(v.element, abort.signal)));
        if (generation !== clock.current.generation) return;
        await Promise.all([...(clip.source_type === 'image' ? [] : [video.play()]), ...syncAudio(toMs(target), true)]);
        if (generation !== clock.current.generation) return;
        clock.current.origin = timeNow() - target / FPS;
        clock.current.playing = true;
        setPlaying(true);
        current.current.onPlaying(true);
      }
      setBuffering(false);
      // Preload only the next picture decoder. It never emits sound.
      const next = rows[rows.indexOf(targetEntry) + 1];
      if (next?.clip.url && next.clip.source_type !== 'image') {
        const other = videos.current[1 - index]!;
        if (other.dataset.mediaId !== next.clip.media_id) {
          other.dataset.mediaId = next.clip.media_id!;
          other.src = next.clip.url;
          other.preload = 'auto';
          other.load();
        }
      }
    } catch (cause) {
      if (generation !== clock.current.generation || abort.signal.aborted) return;
      stop();
      setError(cause instanceof Error ? cause.message : '预览失败，请重试。');
    }
  }
  const actions = useRef({ seek, stop });
  actions.current = { seek, stop };
  function toggle() {
    if (clock.current.playing) stop();
    else void seek(clock.current.at >= total - 1 ? 0 : clock.current.at, true);
  }
  useImperativeHandle(ref, () => ({
    seek: (at) => {
      void seek(at);
    },
    pause: stop,
    toggle,
  }));
  const signature = JSON.stringify([
    props.clips.map((c) => [c.id, c.media_id, c.trim_in_ms, c.trim_out_ms, c.duration_ms, c.included, c.issue]),
    props.sound.document,
  ]);
  useEffect(() => {
    actions.current.stop();
    clearVoices();
    void actions.current.seek(clock.current.at);
  }, [signature]);
  useEffect(() => {
    setDraftTransform(null);
  }, [entry?.clip.id, entry?.clip.transform]);
  useEffect(() => {
    const observer = new ResizeObserver(([value]) =>
      setSize({
        width: value.contentRect.width,
        height: value.contentRect.height,
      }),
    );
    if (box.current) observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let animation = 0;
    const tick = () => {
      if (clock.current.playing) {
        const at = Math.floor((timeNow() - clock.current.origin) * FPS),
          rows = buildTimeline(current.current.clips),
          end = rows.at(-1)?.end ?? 0;
        const row = locateFrame(rows, at);
        if (at >= end) {
          actions.current.stop();
          show(end);
        } else if (!row || row.clip.id !== active.current?.id) {
          void actions.current.seek(at, true);
        } else {
          const video = videos.current[active.current!.slot];
          if (row.clip.source_type !== 'image' && video && (video.readyState < 3 || video.seeking)) {
            void actions.current.seek(clock.current.at, true);
          } else {
            try {
              syncAudio(toMs(at), false);
              if ([...voices.current.values()].some((v) => v.element.readyState < 3 || v.element.seeking)) {
                void actions.current.seek(clock.current.at, true);
              } else {
                show(at);
                void Promise.all(syncAudio(toMs(at), true)).catch((cause) => {
                  actions.current.stop();
                  setError(cause.message || '声音播放失败，请重试。');
                });
              }
            } catch (cause) {
              actions.current.stop();
              setError((cause as Error).message);
            }
          }
        }
      }
      animation = requestAnimationFrame(tick);
    };
    animation = requestAnimationFrame(tick);
    const hide = () => {
      if (document.hidden) actions.current.stop();
    };
    document.addEventListener('visibilitychange', hide);
    return () => {
      cancelAnimationFrame(animation);
      document.removeEventListener('visibilitychange', hide);
      actions.current.stop();
      clearVoices();
      void context.current?.close();
      context.current = null;
    };
  }, []);
  const captions =
    sound.burn_subtitles && !sound.tracks.some((t) => t.id === 'subtitles' && t.muted)
      ? sound.subtitles.filter((s) => toMs(position) >= s.start_ms && toMs(position) < s.end_ms)
      : [];
  const style = sound.subtitle_style,
    column = (style.alignment - 1) % 3,
    row = Math.floor((style.alignment - 1) / 3);
  const captionStyle: CSSProperties = {
    color: style.color,
    fontFamily: props.font,
    fontWeight: style.bold ? 700 : 400,
    fontSize: (sound.font_size * canvasHeight) / 720,
    WebkitTextStroke: `${(style.outline_width * canvasHeight) / 720}px ${style.outline_color}`,
    paintOrder: 'stroke fill',
    textAlign: column === 0 ? 'left' : column === 2 ? 'right' : 'center',
    justifyContent: row === 0 ? 'flex-end' : row === 2 ? 'flex-start' : 'center',
    alignItems: column === 0 ? 'flex-start' : column === 2 ? 'flex-end' : 'center',
    padding: (Math.min(canvasWidth, canvasHeight) * style.margin_percent) / 100,
  };
  return (
    <section className="assembly-player assembly-complete-player" aria-label="音画同步预览" ref={surface}>
      <div className="assembly-screen" ref={box}>
        <div className="assembly-composition" style={{ width: canvasWidth, height: canvasHeight }}>
          {[0, 1].map((index) => (
            <video
              key={index}
              ref={(v) => {
                videos.current[index] = v;
              }}
              muted
              playsInline
              preload="auto"
              className={slot === index && entry?.clip.source_type !== 'image' ? 'is-active' : ''}
              style={geometry}
              onError={() => {
                if (active.current?.slot === index) {
                  stop();
                  setError('视频加载失败，请刷新媒体后重试。');
                }
              }}
            />
          ))}
          {entry?.clip.source_type === 'image' && entry.clip.url && (
            <img
              className="assembly-still"
              src={entry.clip.url}
              alt={entry.clip.script || '画面素材'}
              style={geometry}
              draggable={false}
            />
          )}
          <div className="assembly-caption-overlay" style={captionStyle}>
            {captions.map((s) => (
              <span
                key={s.id}
                style={
                  style.background
                    ? {
                        backgroundColor: `${style.background_color}${Math.round(style.background_opacity * 255)
                          .toString(16)
                          .padStart(2, '0')}`,
                        padding: '0 .2em',
                      }
                    : undefined
                }
              >
                {s.text}
              </span>
            ))}
          </div>
          {safe && <div className="assembly-safe-area" aria-label="字幕安全区" />}
          {cropping && entry && !props.readOnly && (
            <div
              className="assembly-crop-surface"
              aria-label="拖动画面调整位置"
              onPointerDown={(event) => {
                stop();
                event.currentTarget.setPointerCapture(event.pointerId);
                drag.current = {
                  x: event.clientX,
                  y: event.clientY,
                  transform: transform ?? {
                    fit: 'contain',
                    scale: 1,
                    x: 0.5,
                    y: 0.5,
                  },
                };
              }}
              onPointerMove={(event) => {
                if (!drag.current) return;
                const d = drag.current;
                setDraftTransform({
                  ...d.transform,
                  x: Math.max(
                    0,
                    Math.min(1, d.transform.x + (event.clientX - d.x) / (canvasWidth - geometry.width || canvasWidth)),
                  ),
                  y: Math.max(
                    0,
                    Math.min(
                      1,
                      d.transform.y + (event.clientY - d.y) / (canvasHeight - geometry.height || canvasHeight),
                    ),
                  ),
                });
              }}
              onPointerUp={() => {
                if (draftTransform) props.onTransform(entry.clip.id, draftTransform);
                drag.current = null;
              }}
              onPointerCancel={() => {
                drag.current = null;
                setDraftTransform(null);
              }}
            />
          )}
        </div>
        {!total && <div className="assembly-no-video">添加视频或图片开始剪辑</div>}
        {buffering && !error && (
          <div className="assembly-player-message" role="status">
            正在准备画面与声音…
          </div>
        )}
        {error && (
          <div className="assembly-player-message" role="alert">
            <p>{error}</p>
            <Button
              loading={retrying}
              onClick={() => {
                setRetrying(true);
                stop();
                void props
                  .onRefresh()
                  .then(() => {
                    clearVoices();
                    videos.current.forEach((v) => v?.removeAttribute('data-media-id'));
                    return actions.current.seek(clock.current.at);
                  })
                  .catch(() => setError('刷新媒体失败，当前编辑已保留。'))
                  .finally(() => setRetrying(false));
              }}
            >
              刷新媒体并重试
            </Button>
          </div>
        )}
      </div>
      <AssemblyPlaybackControls
        playing={playing}
        disabled={!total}
        position={position}
        total={total}
        muted={muted}
        onToggle={toggle}
        onStep={(d) => {
          void seek(clock.current.at + d);
        }}
        onMute={() => {
          mutedRef.current = !mutedRef.current;
          setMuted(mutedRef.current);
          voices.current.forEach((v) => {
            if (mutedRef.current) v.gain.gain.value = 0;
          });
        }}
        onFullscreen={() => {
          void (document.fullscreenElement ? document.exitFullscreen() : surface.current?.requestFullscreen())?.catch(
            () => setError('当前浏览器不支持全屏。'),
          );
        }}
      >
        <AssemblyAction
          label="构图拖动"
          icon={<Crop size={15} />}
          disabled={props.readOnly}
          aria-pressed={cropping}
          onClick={() => setCropping(!cropping)}
        />
        <AssemblyAction
          label="字幕安全区"
          icon={<Scan size={15} />}
          aria-pressed={safe}
          onClick={() => setSafe(!safe)}
        />
      </AssemblyPlaybackControls>
    </section>
  );
});
