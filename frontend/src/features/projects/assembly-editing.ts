import type { AssemblyClip } from '../../api/modules/assembly';

export function clipDuration(clip: AssemblyClip) { return Math.max(0, (clip.trim_out_ms ?? clip.duration_ms ?? 0) - clip.trim_in_ms); }
export function moveClip(clips: AssemblyClip[], id: string, target: number) {
  const index = clips.findIndex(clip => clip.id === id);
  if (index < 0 || target < 0 || target >= clips.length || index === target) return clips;
  const next = [...clips]; const [clip] = next.splice(index, 1); next.splice(target, 0, clip);
  return next.map((item, position) => ({ ...item, position: position + 1 }));
}
export function nextPlayable(clips: AssemblyClip[], id?: string) {
  const index = id ? clips.findIndex(clip => clip.id === id) : -1;
  return clips.slice(index + 1).find(clip => clip.included && !clip.issue && clip.url);
}
export function timecode(ms: number) {
  const seconds = Math.max(0, ms) / 1000;
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
}

export const FPS = 30;
export const toFrame = (ms: number) => Math.round(ms * FPS / 1000);
export const toMs = (frame: number) => Math.round(frame * 1000 / FPS);
export const snapMs = (ms: number) => toMs(toFrame(ms));
export const clipFrames = (clip: AssemblyClip) => Math.max(0, toFrame(clip.trim_out_ms ?? clip.duration_ms ?? 0) - toFrame(clip.trim_in_ms));
export interface TimelineEntry { clip: AssemblyClip; start: number; end: number }
export function buildTimeline(clips: AssemblyClip[]): TimelineEntry[] {
  let frame = 0;
  return clips.filter(c => c.included).map(clip => {
    const start = frame; frame += clipFrames(clip);
    return { clip, start, end: frame };
  });
}
export function locateFrame(entries: TimelineEntry[], frame: number) {
  return entries.find(entry => frame >= entry.start && frame < entry.end)
    ?? (frame === entries.at(-1)?.end ? entries.at(-1) : undefined);
}
export function splitClip(clips: AssemblyClip[], id: string, frame: number, newId = crypto.randomUUID()) {
  const entry = buildTimeline(clips).find(e => e.clip.id === id);
  if (!entry || frame <= entry.start || frame >= entry.end) return clips;
  const boundary = toMs(toFrame(entry.clip.trim_in_ms) + frame - entry.start);
  const index = clips.findIndex(c => c.id === id);
  const next = [...clips];
  next.splice(index, 1,
    { ...entry.clip, trim_out_ms: boundary },
    { ...entry.clip, id: newId, source_clip_id: entry.clip.source_clip_id ?? id, trim_in_ms: boundary });
  return next.map((c, i) => ({ ...c, position: i + 1 }));
}
export function addClip(clips: AssemblyClip[], source: AssemblyClip, index = clips.length, id = crypto.randomUUID()) {
  const next = [...clips];
  next.splice(index, 0, { ...source, id, source_clip_id: source.source_clip_id ?? source.id,
    included: true, trim_in_ms: 0, trim_out_ms: null });
  return next.map((c, i) => ({ ...c, position: i + 1 }));
}
export function trimClip(clips: AssemblyClip[], id: string, start: number, end: number) {
  const clip = clips.find(c => c.id === id);
  if (!clip?.duration_ms) return clips;
  const first = Math.max(0, toFrame(start)), last = Math.min(toFrame(clip.duration_ms), toFrame(end));
  if (last <= first) return clips;
  return clips.map(c => c.id === id ? { ...c, trim_in_ms: toMs(first), trim_out_ms: Math.min(clip.duration_ms!, toMs(last)) } : c);
}
export function framecode(frame: number) {
  const f = Math.max(0, Math.round(frame));
  return `${Math.floor(f / FPS / 60).toString().padStart(2, '0')}:${Math.floor(f / FPS % 60).toString().padStart(2, '0')}:${(f % FPS).toString().padStart(2, '0')}`;
}
