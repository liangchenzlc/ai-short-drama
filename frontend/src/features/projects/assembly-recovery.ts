import type { AssemblyClip, AssemblyState } from '../../api/modules/assembly';
import { isStoredDraft, type StoredDraft } from './creation-recovery';

export interface AssemblyDraftDocument { assembly_id: string; resolution: '720p' | '1080p'; clips: AssemblyClip[] }
export function assemblyDraft(value: AssemblyState): StoredDraft<AssemblyDraftDocument> {
  return { format: 1, base_version: value.assembly!.row_version, document: {
    assembly_id: value.assembly!.id, resolution: value.assembly!.resolution,
    clips: (value.clips ?? []).map(({ url: _url, poster: _poster, filmstrip: _filmstrip, ...clip }) => ({ ...clip, url: null, poster: null })),
  } };
}
export function validAssemblyDraft(value: unknown): value is StoredDraft<AssemblyDraftDocument> {
  if (!isStoredDraft(value) || !value.document || typeof value.document !== 'object') return false;
  const doc = value.document as Partial<AssemblyDraftDocument>;
  return typeof doc.assembly_id === 'string' && /^[0-9]+$/.test(doc.assembly_id) && ['720p', '1080p'].includes(doc.resolution ?? '') && Array.isArray(doc.clips) && doc.clips.length <= 300 && new Set(doc.clips.map(clip => clip.id)).size === doc.clips.length && doc.clips.every(clip => typeof clip.id === 'string' && typeof clip.shot_id === 'string' && typeof clip.included === 'boolean' && typeof clip.muted === 'boolean' && typeof clip.trim_in_ms === 'number' && Number.isFinite(clip.trim_in_ms) && clip.trim_in_ms >= 0 && (clip.trim_out_ms === null || typeof clip.trim_out_ms === 'number' && Number.isFinite(clip.trim_out_ms)));
}
export function restoreAssemblyDraft(value: AssemblyState, draft: StoredDraft<AssemblyDraftDocument>): AssemblyState {
  const sources = [...value.clips ?? [], ...value.sources ?? []];
  return { ...value, assembly: { ...value.assembly!, resolution: draft.document.resolution }, clips: draft.document.clips.map((clip, index) => {
    const source = sources.find(item => item.media_id === clip.media_id && item.shot_id === clip.shot_id);
    return { ...clip, url: source?.url ?? null, poster: source?.poster ?? null, filmstrip: source?.filmstrip ?? null,
      is_stale: source?.is_stale ?? true, archived: source?.archived ?? clip.archived,
      issue: source?.issue ?? (source ? null : 'missing'), position: index + 1,
    };
  }) };
}
