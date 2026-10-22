import type { AssemblyClip, AssemblyState } from '../../api/modules/assembly';
import type { SoundDocument } from '../../api/modules/sound';
import { isStoredDraft, type StoredDraft } from './creation-recovery';
import { materialClip } from './assembly-document';

export interface AssemblyDraftDocument { assembly_id: string; aspect: 'auto' | '16:9' | '9:16'; resolution: '720p' | '1080p'; clips: AssemblyClip[]; sound?: SoundDocument }
export const assemblyVersion = (value: AssemblyState) => value.editor_version === 2 ? ((BigInt(value.assembly?.row_version ?? '0') << 64n) + BigInt(value.sound?.row_version ?? '0')).toString() : value.assembly?.row_version ?? '0';
export function normalizeAssemblyRecovery(draft: StoredDraft<AssemblyDraftDocument>) {
  const version = (value: string) => value.includes(':') ? value.split(':').map(BigInt).reduce((a, b) => (a << 64n) + b).toString() : value;
  const stored = draft as StoredDraft<AssemblyDraftDocument> & { pending?: { base_version: string; document: AssemblyDraftDocument } };
  return { ...stored, base_version: version(stored.base_version), ...(stored.pending ? { pending: { ...stored.pending, base_version: version(stored.pending.base_version) } } : {}) };
}
export function assemblyDraft(value: AssemblyState): StoredDraft<AssemblyDraftDocument> {
  return { format: 1, base_version: assemblyVersion(value), document: {
    assembly_id: value.assembly!.id, aspect: value.assembly!.aspect, resolution: value.assembly!.resolution,
    clips: (value.clips ?? []).map(({ url: _url, poster: _poster, filmstrip: _filmstrip, ...clip }) => ({ ...clip, url: null, poster: null })),
    ...(value.sound ? { sound: value.sound.document } : {}),
  } };
}
export function validAssemblyDraft(value: unknown): value is StoredDraft<AssemblyDraftDocument> {
  if (!value || typeof value !== 'object') return false;
  const draft = value as StoredDraft<AssemblyDraftDocument>;
  if (typeof draft.base_version !== 'string' || !/^\d+(?::\d+)?$/.test(draft.base_version) || !isStoredDraft({ ...draft, base_version: draft.base_version.split(':')[0] }) || !draft.document || typeof draft.document !== 'object') return false;
  const doc = draft.document as Partial<AssemblyDraftDocument>;
  return typeof doc.assembly_id === 'string' && /^[0-9]+$/.test(doc.assembly_id) && ['auto', '16:9', '9:16'].includes(doc.aspect ?? '') && ['720p', '1080p'].includes(doc.resolution ?? '') && (!doc.sound || Array.isArray(doc.sound.audio_clips) && Array.isArray(doc.sound.subtitles)) && Array.isArray(doc.clips) && doc.clips.length <= 300 && new Set(doc.clips.map(clip => clip.id)).size === doc.clips.length && doc.clips.every(clip => typeof clip.id === 'string' && (clip.shot_id === null || typeof clip.shot_id === 'string') && typeof clip.included === 'boolean' && typeof clip.muted === 'boolean' && typeof clip.trim_in_ms === 'number' && Number.isFinite(clip.trim_in_ms) && clip.trim_in_ms >= 0 && (clip.trim_out_ms === null || typeof clip.trim_out_ms === 'number' && Number.isFinite(clip.trim_out_ms)));
}
export function restoreAssemblyDraft(value: AssemblyState, draft: StoredDraft<AssemblyDraftDocument>): AssemblyState {
  const sources = [...value.clips ?? [], ...value.sources ?? [], ...(value.materials ?? []).filter(m => m.kind !== 'audio').map(materialClip)];
  return { ...value, sound: value.sound && draft.document.sound ? { ...value.sound, document: draft.document.sound } : value.sound, assembly: { ...value.assembly!, aspect: draft.document.aspect, resolution: draft.document.resolution }, clips: draft.document.clips.map((clip, index) => {
    const source = sources.find(item => item.media_id === clip.media_id);
    return { ...clip, url: source?.url ?? null, poster: source?.poster ?? null, filmstrip: source?.filmstrip ?? null,
      is_stale: source?.is_stale ?? true, archived: source?.archived ?? clip.archived,
      issue: source?.issue ?? (source ? null : 'missing'), position: index + 1,
    };
  }) };
}
