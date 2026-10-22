import type { AssetDraft, AssetKind, LibraryAssetRead } from '../../api/modules/assets';
import type { CreationDraftValue } from '../../api/modules/creation-drafts';

export interface LocalAssetDraft extends Omit<AssetDraft, 'kind'> {
  id: string;
  kind: AssetKind | null;
  status: 'local' | 'saving' | 'error' | 'unknown';
  message: string;
  attempt: { key: string; body: AssetDraft } | null;
  generation?: CreationDraftValue;
}
export function blankAssetDraft(kind: AssetKind | 'all'): LocalAssetDraft {
  return { id: `draft:${crypto.randomUUID()}`, kind: kind === 'all' ? null : kind, name: '',
    label: '', description: '', prompt: '', tags: [], scene_time: '', status: 'local', message: '', attempt: null };
}
export function assetDraftHasInput(draft: LocalAssetDraft) {
  return !!(draft.name || draft.description || draft.prompt || draft.label || draft.tags.length || draft.scene_time
    || draft.generation?.input_text || draft.generation?.model_config_id || draft.generation?.reference_media_ids.length);
}
export function assetDraftBody(draft: LocalAssetDraft): AssetDraft | null {
  if (!draft.name.trim() || !draft.kind) return null;
  return { kind: draft.kind, name: draft.name.trim(), label: draft.label,
    description: draft.description, prompt: draft.prompt, tags: draft.tags, scene_time: draft.scene_time };
}
export function assetCreationAttempt(draft: LocalAssetDraft) {
  // Unknown acceptance keeps the exact body and key, even if editing continues.
  if (draft.attempt && (draft.status === 'unknown' || draft.status === 'saving')) return draft.attempt;
  const body = assetDraftBody(draft);
  if (!body) return null;
  return { key: draft.attempt && JSON.stringify(draft.attempt.body) === JSON.stringify(body)
    ? draft.attempt.key : crypto.randomUUID(), body };
}
export function assetCreationUnknown(cause: unknown) {
  const status = (cause as { status?: number } | null)?.status;
  return status === undefined || status >= 500 || status === 408;
}
export function mergeCreatedAsset(created: LibraryAssetRead, draft: LocalAssetDraft): LibraryAssetRead {
  return { ...created, ...(draft.kind ? { kind: draft.kind } : {}), name: draft.name,
    label: draft.label, description: draft.description, prompt: draft.prompt, tags: draft.tags, scene_time: draft.scene_time };
}
export function localAssetDraftKey(actorId: string, projectId: string) {
  return `short-drama:asset-drafts:v1:${actorId}:${projectId}`;
}
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const identifier = (value: unknown) => typeof value === 'string' && /^\d+$/.test(value);
const optionalOneOf = (values: readonly string[], value: unknown) => value == null || typeof value === 'string' && values.includes(value);
const imageAspects = ['16:9', '9:16', '1:1', '4:3', '3:4'] as const;
const imageResolutions = ['1K', '2K', '4K'] as const;
const videoResolutions = ['480p', '720p', '1080p'] as const;
function validGeneration(value: unknown): value is CreationDraftValue {
  if (!record(value)) return false;
  const image = value.image_settings;
  const video = value.video_settings;
  return (value.model_config_id === null || identifier(value.model_config_id))
    && typeof value.input_text === 'string'
    && (image === null || record(image) && [1, 2, 4].includes(image.count as number)
      && optionalOneOf(imageAspects, image.aspect) && optionalOneOf(imageResolutions, image.resolution))
    && (video === null || record(video) && optionalOneOf(imageAspects, video.aspect) && optionalOneOf(videoResolutions, video.resolution)
      && (video.duration_ms == null || Number.isInteger(video.duration_ms) && (video.duration_ms as number) >= 1000
        && (video.duration_ms as number) <= 3600000 && (video.duration_ms as number) % 1000 === 0))
    && Array.isArray(value.reference_media_ids) && value.reference_media_ids.length <= 16
    && value.reference_media_ids.every(identifier);
}
export function readLocalAssetDrafts(key: string): LocalAssetDraft[] {
  const raw = localStorage.getItem(key);
  if (!raw) return [];
  const values: unknown = JSON.parse(raw);
  if (!Array.isArray(values) || values.some(value => !value || typeof value !== 'object'
    || typeof value.id !== 'string' || !value.id.startsWith('draft:')
    || !['name', 'label', 'description', 'prompt', 'scene_time', 'message'].every(key => typeof value[key] === 'string')
    || !Array.isArray(value.tags) || !value.tags.every((tag: unknown) => typeof tag === 'string')
    || !['local', 'saving', 'error', 'unknown'].includes(value.status)
    || !['character', 'scene', 'prop', null].includes(value.kind)
    || value.generation !== undefined && !validGeneration(value.generation)
    || value.attempt && (typeof value.attempt.key !== 'string' || !value.attempt.body || typeof value.attempt.body.name !== 'string')))
    throw new Error('素材草稿恢复记录损坏');
  return values.map(value => ({ ...value, status: value.status === 'saving' ? 'unknown' : value.status,
    message: value.status === 'saving' ? '创建受理结果待核对，请使用原请求恢复。' : value.message }));
}
