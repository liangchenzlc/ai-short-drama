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
