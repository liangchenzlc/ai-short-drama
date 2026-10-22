import type { AudioClip } from '../../api/modules/sound';
import type { FrameTransform } from '../../api/modules/assembly';
import type { EditorSound } from './assembly-document';

export function audibleAudio(sound: EditorSound) {
  const muted = new Set(sound.tracks.filter(t => t.muted).map(t => t.id));
  return sound.audio_clips.filter(c => !c.muted && !muted.has(c.kind));
}
export function audioGain(clip: AudioClip, at: number, sound: EditorSound) {
  const offset = at - clip.start_ms;
  if (offset < 0 || offset >= clip.duration_ms) return 0;
  const fade = Math.min(1, clip.fade_in_ms ? offset / Math.min(clip.duration_ms, clip.fade_in_ms) : 1,
    clip.fade_out_ms ? (clip.duration_ms - offset) / Math.min(clip.duration_ms, clip.fade_out_ms) : 1);
  const voices = sound.native_ducking?.length ? sound.native_ducking : audibleAudio(sound).filter(c => c.kind === 'dialogue').map(c => ({ start_ms: c.start_ms, end_ms: c.start_ms + c.duration_ms }));
  const duck = clip.kind === 'music' && clip.ducking && voices.some(v => at >= v.start_ms && at <= v.end_ms) ? .25 : 1;
  return clip.volume * 10 ** (clip.gain_db / 20) * fade * duck * (clip.kind === 'original' ? sound.original_volume : clip.kind === 'dialogue' ? sound.dialogue_volume : 1);
}
export function audioSourceTime(clip: AudioClip, at: number) {
  const offset = Math.max(0, at - clip.start_ms);
  return (clip.trim_in_ms + (clip.loop ? (offset + (clip.loop_offset_ms ?? 0)) % Math.max(1, clip.trim_out_ms - clip.trim_in_ms) : offset)) / 1000;
}
export function waveformSlice(values: number[], clip: AudioClip, sourceDuration: number, count = 160) {
  if (!values.length || sourceDuration <= 0) return [];
  return Array.from({ length: Math.max(1, Math.min(512, count)) }, (_, i) => {
    const offset = clip.duration_ms * (i + .5) / Math.max(1, Math.min(512, count));
    const source = clip.trim_in_ms + (clip.loop ? (offset + (clip.loop_offset_ms ?? 0)) % Math.max(1, clip.trim_out_ms - clip.trim_in_ms) : offset);
    return values[Math.max(0, Math.min(values.length - 1, Math.floor(source / sourceDuration * values.length)))];
  });
}
export function frameGeometry(transform: FrameTransform | null | undefined, width: number, height: number, sourceWidth: number, sourceHeight: number) {
  const t = transform ?? { fit: 'contain', scale: 1, x: .5, y: .5 };
  const ratio = (t.fit === 'cover' ? Math.max : Math.min)(width / Math.max(1, sourceWidth), height / Math.max(1, sourceHeight)) * t.scale;
  const w = sourceWidth * ratio, h = sourceHeight * ratio;
  return { width: w, height: h, left: (width - w) * t.x, top: (height - h) * t.y };
}
