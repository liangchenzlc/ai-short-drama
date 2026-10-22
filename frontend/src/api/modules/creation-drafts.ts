import { http } from '../http';

export type CreationPurpose = 'novel_adaptation' | 'asset_image' | 'storyboard_image' | 'storyboard_video';
export interface CreationDraftValue {
  model_config_id: string | null;
  input_text: string;
  image_settings: { aspect?: string | null; resolution?: string | null; count: number } | null;
  video_settings: { aspect?: string | null; resolution?: string | null; duration_ms?: number | null } | null;
  reference_media_ids: string[];
}
export interface UserCreationDraft extends CreationDraftValue {
  id: string; project_id: string;  object_id: string;
  purpose: CreationPurpose; row_version: string; draft_version: string;
  source_version: string | null;
  reference_images?: { media_id: string; url: string | null; width?: number | null; height?: number | null }[];
}
export interface CreationDraftTransport {
  get(): Promise<UserCreationDraft | null>;
  create(value: CreationDraftValue): Promise<UserCreationDraft>;
  update(version: string, value: CreationDraftValue): Promise<UserCreationDraft>;
}
export function creationDraftsApi(projectId: string, objectId: string, purpose: CreationPurpose): CreationDraftTransport {
  const root = `/projects/${encodeURIComponent(projectId)}/drafts`;
  const target = `${root}/${encodeURIComponent(objectId)}/${purpose}`;
  return {
    async get() { return (await http.get<UserCreationDraft | null>(target)).data; },
    async create(value) { return (await http.post<UserCreationDraft>(root, { ...value, object_id: objectId, purpose })).data; },
    async update(row_version, value) { return (await http.patch<UserCreationDraft>(target, { ...value, row_version })).data; },
  };
}
