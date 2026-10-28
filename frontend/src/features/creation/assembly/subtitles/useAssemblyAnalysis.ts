import { errorMessage } from '@host/api/http';
import type { useAssembly } from '@host/features/creation/assembly/workbench/useAssembly';
import type { MediaJob } from '@host/types/creation/assembly/document';
import type { AudioClip } from '@host/types/creation/sound/tracks';
import { useEffect, useRef, useState } from 'react';
export function useAssemblyAnalysis(editor: ReturnType<typeof useAssembly>) {
  const [waveforms, setWaveforms] = useState<Record<string, number[]>>({}),
    [error, setError] = useState('');
  const attempted = useRef(new Set<string>()),
    results = useRef(new Map<string, NonNullable<MediaJob['result']>>());
  const [epoch, setEpoch] = useState(0);
  const sound = editor.value?.sound,
    audio = sound?.document.audio_clips ?? [];
  const signature = [...new Set(audio.map((c) => `${c.media_id}:${sound?.media[c.media_id]?.duration_ms ?? 0}`))]
    .sort()
    .join(',');
  async function analyse(clip: Pick<AudioClip, 'media_id' | 'trim_in_ms' | 'trim_out_ms'>, signal?: AbortSignal) {
    const key = `${clip.media_id}:${clip.trim_in_ms}:${clip.trim_out_ms}`,
      cached = results.current.get(key);
    if (cached) return cached;
    let job = await editor.api.analyse(clip, crypto.randomUUID());
    while (['queued', 'running'].includes(job.status)) {
      await new Promise<void>((resolve, reject) => {
        const done = () => {
          signal?.removeEventListener('abort', abort);
          resolve();
        };
        const timer = setTimeout(done, 1800);
        const abort = () => {
          clearTimeout(timer);
          reject(new DOMException('Cancelled', 'AbortError'));
        };
        signal?.addEventListener('abort', abort, { once: true });
        if (signal?.aborted) abort();
      });
      job = await editor.api.mediaJob(job.id, signal);
    }
    if (job.status !== 'succeeded' || !job.result)
      throw new Error(job.error?.message || '声音分析未完成，请检查媒体处理 Worker');
    results.current.set(key, job.result);
    return job.result;
  }
  useEffect(() => {
    const controller = new AbortController();
    const running = new Set<string>();
    const entries = [...new Set(audio.map((c) => c.media_id))].filter((id) => !attempted.current.has(id));
    const run = async () => {
      for (const media_id of entries) {
        if (controller.signal.aborted) return;
        const duration = sound?.media[media_id]?.duration_ms;
        if (!duration) continue;
        attempted.current.add(media_id);
        running.add(media_id);
        try {
          const result = await analyse({ media_id, trim_in_ms: 0, trim_out_ms: duration }, controller.signal);
          if (!controller.signal.aborted && result.waveform)
            setWaveforms((value) => ({
              ...value,
              [media_id]: result.waveform!,
            }));
        } catch (cause) {
          attempted.current.delete(media_id);
          if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : errorMessage(cause));
        } finally {
          running.delete(media_id);
        }
      }
    };
    const timer = setTimeout(() => {
      void run();
    }, 500);
    return () => {
      clearTimeout(timer);
      running.forEach((id) => attempted.current.delete(id));
      controller.abort();
    };
  }, [signature, editor.api, epoch]);
  return {
    waveforms,
    analyse,
    error,
    clearError: () => setError(''),
    retry: () => {
      setError('');
      attempted.current.clear();
      setEpoch((v) => v + 1);
    },
  };
}
