import { buildTimeline, toMs } from '@host/features/creation/assembly/sessions/assembly-editing';
import type { AssemblyClip, AssemblyMaterial, AssemblyState } from '@host/types/creation/assembly/document';
import type { EditorSound } from '@host/types/creation/assembly/sessions/assembly-document';
import type { AudioClip, SoundDocument, Subtitle, SubtitleStyle } from '@host/types/creation/sound/tracks';
export const subtitleStyle: SubtitleStyle = {
  color: '#ffffff',
  outline_color: '#000000',
  outline_width: 2,
  background: false,
  background_color: '#000000',
  background_opacity: 0.5,
  alignment: 2,
  margin_percent: 5,
  bold: false,
};
export const emptySound = (): EditorSound => ({
  schema_version: 2,
  dialogue: [],
  subtitles: [],
  native_ducking: [],
  music: null,
  original_volume: 1,
  dialogue_volume: 1,
  burn_subtitles: false,
  font_size: 24,
  audio_clips: [],
  tracks: [],
  subtitle_style: { ...subtitleStyle },
});
function stableId(seed: string) {
  let hash = 2166136261;
  const parts = Array.from({ length: 4 }, (_, i) => {
    for (const char of `${i}:${seed}`) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    return (hash >>> 0).toString(16).padStart(8, '0');
  }).join('');
  return `${parts.slice(0, 8)}-${parts.slice(8, 12)}-4${parts.slice(13, 16)}-8${parts.slice(17, 20)}-${parts.slice(20)}`;
}
export const clipSpans = (clips: AssemblyClip[]) =>
  buildTimeline(clips).map((e) => ({
    clip: e.clip,
    start: toMs(e.start),
    end: toMs(e.end),
  }));
export function anchorAt(clips: AssemblyClip[], start: number) {
  const span = clipSpans(clips).find((s) => start >= s.start && start < s.end);
  return span
    ? {
        anchor_clip_id: span.clip.id,
        anchor_source_in_ms: span.clip.trim_in_ms + start - span.start,
      }
    : { anchor_clip_id: null, anchor_source_in_ms: null };
}
export function sourceAudio(clip: AssemblyClip, start: number, duration: number): AudioClip {
  const end = Math.min(clip.duration_ms ?? Infinity, clip.trim_in_ms + duration);
  return {
    id: stableId(`original:${clip.id}`),
    name: clip.script || `镜头 ${clip.shot_position}`,
    kind: 'original',
    media_id: clip.media_id!,
    start_ms: start,
    duration_ms: end - clip.trim_in_ms,
    trim_in_ms: clip.trim_in_ms,
    trim_out_ms: end,
    volume: 1,
    loop: false,
    fade_in_ms: 0,
    fade_out_ms: 0,
    ducking: false,
    muted: clip.muted,
    gain_db: 0,
    anchor_clip_id: clip.id,
    anchor_source_in_ms: clip.trim_in_ms,
  };
}
export function normalizeSound(
  document: SoundDocument | undefined,
  clips: AssemblyClip[],
  media: Record<string, { duration_ms: number }> = {},
): EditorSound {
  const doc = {
    ...emptySound(),
    ...document,
    tracks: document?.tracks ?? [],
    subtitle_style: { ...subtitleStyle, ...document?.subtitle_style },
  };
  const audio =
    document?.schema_version === 2
      ? (document.audio_clips ?? [])
      : clipSpans(clips)
          .filter(
            (s) => s.clip.media_id && s.clip.source_type !== 'image' && s.clip.has_audio !== false && s.end > s.start,
          )
          .map((s) => sourceAudio(s.clip, s.start, s.end - s.start));
  if (document?.schema_version !== 2) {
    for (const line of doc.dialogue) {
      const duration = line.media_id ? media[line.media_id]?.duration_ms : 0;
      if (line.media_id && duration)
        audio.push({
          id: stableId(`dialogue:${line.id}`),
          name: line.character,
          kind: 'dialogue',
          dialogue_id: line.id,
          media_id: line.media_id,
          start_ms: line.start_ms,
          duration_ms: duration,
          trim_in_ms: 0,
          trim_out_ms: duration,
          volume: 1,
          loop: false,
          fade_in_ms: 0,
          fade_out_ms: 0,
          ducking: false,
          muted: false,
          gain_db: 0,
          ...anchorAt(clips, line.start_ms),
        });
    }
    if (doc.music) {
      const music = doc.music,
        total = clipSpans(clips).at(-1)?.end ?? 0;
      audio.push({
        ...music,
        id: stableId(`music:${music.media_id}`),
        kind: 'music',
        name: '配乐',
        duration_ms: music.loop ? Math.max(1, total - music.start_ms) : music.trim_out_ms - music.trim_in_ms,
        muted: false,
        gain_db: 0,
      });
    }
  }
  return {
    ...doc,
    schema_version: 2,
    music: null,
    audio_clips: audio,
    subtitles: doc.subtitles.map((s, index) => ({
      ...s,
      id: s.id ?? stableId(`subtitle:${index}:${s.start_ms}:${s.text}`),
      ...(document?.schema_version === 2 ? {} : anchorAt(clips, s.start_ms)),
    })),
  };
}
export function normalizeEditor(state: AssemblyState): AssemblyState {
  if (!state.sound || !state.assembly) return state;
  return {
    ...state,
    sound: {
      ...state.sound,
      document: normalizeSound(state.sound.document, state.clips ?? [], state.sound.media),
    },
  };
}

/** Rebuild associations from source coordinates, so repeated edits never accumulate rounding drift. */
export function followClips(before: AssemblyClip[], after: AssemblyClip[], document: EditorSound): EditorSound {
  const oldSpans = clipSpans(before),
    nextSpans = clipSpans(after);
  function slices(item: Subtitle | AudioClip) {
    if (!item.anchor_clip_id) return null;
    const old = oldSpans.find((s) => s.clip.id === item.anchor_clip_id);
    if (!old) return [];
    const primary = nextSpans.find((s) => s.clip.id === old.clip.id);
    const split = primary && primary.clip.trim_out_ms !== old.clip.trim_out_ms;
    const candidates = nextSpans.filter(
      (s) =>
        s.clip.id === old.clip.id ||
        (split &&
          !before.some((c) => c.id === s.clip.id) &&
          s.clip.source_clip_id === (old.clip.source_clip_id ?? old.clip.id) &&
          s.clip.media_id === old.clip.media_id),
    );
    const sourceStart = item.anchor_source_in_ms ?? old.clip.trim_in_ms + item.start_ms - old.start;
    const length = 'duration_ms' in item ? item.duration_ms : item.end_ms - item.start_ms;
    return candidates.flatMap((span) => {
      const a = Math.max(sourceStart, span.clip.trim_in_ms),
        b = Math.min(sourceStart + length, span.clip.trim_in_ms + span.end - span.start);
      return b > a ? [{ span, a, b, cut: a - sourceStart }] : [];
    });
  }
  const audio_clips = document.audio_clips.flatMap((item) => {
    const old = oldSpans.find((s) => s.clip.id === item.anchor_clip_id);
    const wholeOriginal =
      item.kind === 'original' &&
      old &&
      Math.abs(item.start_ms - old.start) <= 1 &&
      Math.abs(item.trim_in_ms - old.clip.trim_in_ms) <= 1 &&
      Math.abs(
        item.duration_ms - Math.min(old.end - old.start, (old.clip.duration_ms ?? Infinity) - old.clip.trim_in_ms),
      ) <= 1;
    if (wholeOriginal) {
      const primary = nextSpans.find((s) => s.clip.id === old.clip.id),
        split = primary && primary.clip.trim_out_ms !== old.clip.trim_out_ms;
      return nextSpans
        .filter(
          (s) =>
            s.clip.id === old.clip.id ||
            (split &&
              !before.some((c) => c.id === s.clip.id) &&
              s.clip.source_clip_id === (old.clip.source_clip_id ?? old.clip.id) &&
              s.clip.media_id === old.clip.media_id),
        )
        .filter((s) => s.clip.media_id && s.clip.source_type !== 'image' && s.clip.has_audio !== false)
        .map((s, i) => ({
          ...item,
          ...sourceAudio(s.clip, s.start, s.end - s.start),
          id: i ? stableId(`${item.id}:${s.clip.id}`) : item.id,
          volume: item.volume,
          gain_db: item.gain_db,
          fade_in_ms: item.fade_in_ms,
          fade_out_ms: item.fade_out_ms,
          muted: s.clip.muted !== old.clip.muted ? s.clip.muted : item.muted,
        }));
    }
    const replacement = nextSpans.find(
      (s) => s.clip.id === item.anchor_clip_id && s.clip.media_id !== item.media_id && item.kind === 'original',
    );
    if (replacement)
      return replacement.clip.source_type === 'image' ||
        replacement.clip.has_audio === false ||
        !replacement.clip.media_id
        ? []
        : [
            {
              ...sourceAudio(replacement.clip, replacement.start, replacement.end - replacement.start),
              id: item.id,
              volume: item.volume,
              gain_db: item.gain_db,
            },
          ];
    const parts = slices(item);
    if (parts === null) return [item];
    return parts.map(({ span, a, b, cut }, i) => ({
      ...item,
      id: i ? stableId(`${item.id}:${span.clip.id}`) : item.id,
      anchor_clip_id: span.clip.id,
      anchor_source_in_ms: a,
      start_ms: span.start + a - span.clip.trim_in_ms,
      duration_ms: b - a,
      ...(item.loop
        ? {
            loop_offset_ms: ((item.loop_offset_ms ?? 0) + cut) % (item.trim_out_ms - item.trim_in_ms),
          }
        : {}),
      trim_in_ms: item.loop ? item.trim_in_ms : item.trim_in_ms + cut,
      trim_out_ms: item.loop ? item.trim_out_ms : item.trim_in_ms + cut + b - a,
      muted: item.kind === 'original' && old?.clip.muted !== span.clip.muted ? span.clip.muted : item.muted,
    }));
  });
  for (const span of nextSpans) {
    if (
      !before.some((c) => c.id === span.clip.id) &&
      !audio_clips.some((c) => c.kind === 'original' && c.anchor_clip_id === span.clip.id) &&
      span.clip.media_id &&
      span.clip.source_type !== 'image' &&
      span.clip.has_audio !== false
    )
      audio_clips.push(sourceAudio(span.clip, span.start, span.end - span.start));
  }
  const subtitles = document.subtitles
    .flatMap((item) => {
      const parts = slices(item);
      return parts === null
        ? [item]
        : parts.map(({ span, a, b }, i) => ({
            ...item,
            id: i ? stableId(`${item.id}:${span.clip.id}`) : item.id,
            anchor_clip_id: span.clip.id,
            anchor_source_in_ms: a,
            start_ms: span.start + a - span.clip.trim_in_ms,
            end_ms: span.start + b - span.clip.trim_in_ms,
          }));
    })
    .sort((a, b) => a.start_ms - b.start_ms);
  return { ...document, audio_clips, subtitles };
}
export function splitAudio(clips: AudioClip[], id: string, at: number, nextId: string): AudioClip[] {
  return clips.flatMap((c) => {
    if (c.id !== id || at <= c.start_ms || at >= c.start_ms + c.duration_ms) return [c];
    const offset = at - c.start_ms;
    return [
      {
        ...c,
        duration_ms: offset,
        trim_out_ms: c.loop ? c.trim_out_ms : c.trim_in_ms + offset,
        fade_out_ms: 0,
      },
      {
        ...c,
        id: nextId,
        start_ms: at,
        duration_ms: c.duration_ms - offset,
        trim_in_ms: c.loop ? c.trim_in_ms : c.trim_in_ms + offset,
        ...(c.loop
          ? {
              loop_offset_ms: ((c.loop_offset_ms ?? 0) + offset) % (c.trim_out_ms - c.trim_in_ms),
            }
          : {}),
        anchor_source_in_ms: c.anchor_source_in_ms == null ? null : c.anchor_source_in_ms + offset,
        fade_in_ms: 0,
      },
    ];
  });
}
export function materialClip(material: AssemblyMaterial): AssemblyClip {
  return {
    id: crypto.randomUUID(),
    shot_id: null,
    media_id: material.media_id,
    source_type: material.kind === 'image' ? 'image' : 'video',
    display_duration_ms: material.kind === 'image' ? 5000 : null,
    position: 1,
    shot_position: 0,
    included: true,
    muted: false,
    trim_in_ms: 0,
    trim_out_ms: null,
    duration_ms: material.kind === 'image' ? 5000 : material.duration_ms,
    script: material.name,
    url: material.url,
    poster: material.kind === 'image' ? material.url : material.poster,
    is_stale: false,
    archived: false,
    issue: null,
    has_audio: material.has_audio,
    width: material.width,
    height: material.height,
  };
}
export function resegmentSubtitles(subtitles: Subtitle[], maxLength = 20): Subtitle[] {
  return subtitles.flatMap((s) => {
    const pieces = s.text.match(
      new RegExp(`[^。！？!?；;，,\n]{1,${maxLength}}[。！？!?；;，,]?|[。！？!?；;，,]`, 'gu'),
    ) ?? [s.text];
    const total = pieces.reduce((sum, p) => sum + p.length, 0);
    let cursor = s.start_ms,
      count = 0;
    return pieces
      .map((text, i) => {
        count += text.length;
        const end =
          i === pieces.length - 1 ? s.end_ms : s.start_ms + Math.round(((s.end_ms - s.start_ms) * count) / total);
        const item = {
          ...s,
          id: i ? crypto.randomUUID() : s.id,
          text,
          start_ms: cursor,
          end_ms: end,
          anchor_source_in_ms: s.anchor_source_in_ms == null ? null : s.anchor_source_in_ms + cursor - s.start_ms,
        };
        cursor = end;
        return item;
      })
      .filter((p) => p.end_ms > p.start_ms);
  });
}

/** Render and live preview share the requested output ratio, including arbitrary video dimensions. */
export function assemblyOutputAspect(value: AssemblyState | null | undefined): string {
  const assembly = value?.assembly;
  if (assembly?.aspect && assembly.aspect !== 'auto') return assembly.aspect;
  if (assembly?.output_width && assembly.output_height) return `${assembly.output_width}:${assembly.output_height}`;
  const videos = (value?.clips ?? []).filter(
    (clip) => clip.included && clip.source_type !== 'image' && !clip.issue && clip.width && clip.height,
  );
  const source = videos.find((clip) => !clip.source_type || clip.source_type === 'shot') ?? videos[0];
  return source?.width && source.height ? `${source.width}:${source.height}` : '16:9';
}
export function aspectRatio(aspect: string): number {
  const [width, height] = aspect.split(':').map(Number);
  return width > 0 && height > 0 ? width / height : 16 / 9;
}
