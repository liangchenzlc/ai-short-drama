import { http } from '../http';

export interface AssemblyClip {
  filmstrip?: { url: string | null; count: number; interval_ms: number } | null;
  source_clip_id?: string;
  id: string; shot_id: string; media_id: string | null; position: number; shot_position: number;
  included: boolean; muted: boolean; trim_in_ms: number; trim_out_ms: number | null; duration_ms: number | null;
  script: string; url: string | null; poster: string | null; is_stale: boolean; archived: boolean;
  issue: 'missing' | 'invalid' | 'preparing' | 'trim' | null;
}
export interface RenderJob {
  can_cancel?: boolean; can_retry?: boolean; initiated_by?: string | null;
  id: string; kind: 'probe' | 'export' | 'preview'; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  stage: string; progress: number; cancel_requested: boolean; error: { message: string } | null;
  created_at: string; finished_at: string | null; context_hash: string; url: string | null;
  media_id: string | null; duration_ms: number | null; is_stale: boolean;
  aspect?: string; resolution?: string;
  timeline?: { clip_id: string; shot_id: string; trim_in_ms: number; trim_out_ms: number; muted: boolean;
    media_id?: string | null; duration_ms?: number | null; url?: string | null; poster?: string | null;
    filmstrip?: AssemblyClip['filmstrip']; }[];
}
export interface AssemblyState {
  assembly: { id: string; row_version: string; aspect: string; resolution: '720p' | '1080p'; current_media_id: string | null } | null;
  source_hash: string; source_count?: number; context_hash?: string; clips?: AssemblyClip[];
  sources?: AssemblyClip[];
  changes?: { shot_id: string; position: number; kind: 'added' | 'replacement' | 'archived' }[]; jobs?: RenderJob[];
  current_work?: AssemblyCurrentWork | null;
}
export interface AssemblyCurrentWork {
  media_id: string; url: string | null; width: number | null; height: number | null;
  duration_ms: number | null; is_stale: boolean;
}
export const editBody = (value: AssemblyState) => ({ row_version: value.assembly!.row_version, resolution: value.assembly!.resolution,
  clips: (value.clips ?? []).map(({ id, source_clip_id, included, muted, trim_in_ms, trim_out_ms }) => ({ id, source_clip_id, included, muted, trim_in_ms, trim_out_ms })) });
export interface RenderBody { row_version: string; source_hash: string; acknowledge_stale_source: boolean }
export const renderBody = (value: AssemblyState, acknowledge: boolean): RenderBody => ({ row_version: value.assembly!.row_version, source_hash: value.source_hash, acknowledge_stale_source: acknowledge });
export function assemblyApi(projectId: string, episodeId: string) {
  const root = `/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}/assembly`;
  let saveRequest: { fingerprint: string; request_id: string } | null = null;
  return {
    get: async (signal?: AbortSignal) => (await http.get<AssemblyState>(root, { signal })).data,
    initialize: async () => (await http.post<AssemblyState>(`${root}/initialize`)).data,
    save: async (value: AssemblyState) => {
      const body = editBody(value), fingerprint = JSON.stringify(body);
      if (saveRequest?.fingerprint !== fingerprint) saveRequest = { fingerprint, request_id: crypto.randomUUID() };
      const saved = (await http.patch<AssemblyState>(root, { ...body, request_id: saveRequest.request_id })).data;
      saveRequest = null;
      return saved;
    },
    sync: async (value: AssemblyState) => (await http.post<AssemblyState>(`${root}/sync`, { row_version: value.assembly!.row_version, source_hash: value.source_hash })).data,
    export: async (value: AssemblyState, acknowledge: boolean, key: string) => (await http.post<RenderJob>(`${root}/exports`, { row_version: value.assembly!.row_version, source_hash: value.source_hash, acknowledge_stale_source: acknowledge }, { headers: { 'Idempotency-Key': key } })).data,
    preview: async (value: AssemblyState, acknowledge: boolean, key: string) => (await http.post<RenderJob>(`${root}/previews`, { row_version: value.assembly!.row_version, source_hash: value.source_hash, acknowledge_stale_source: acknowledge }, { headers: { 'Idempotency-Key': key } })).data,
    job: async (id: string, signal?: AbortSignal) => (await http.get<RenderJob>(`${root}/exports/${id}`, { signal })).data,
    history: async (offset = 0) => (await http.get<{ items: RenderJob[]; has_more: boolean }>(`${root}/exports`, { params: { offset, limit: 20 } })).data,
    cancel: async (id: string) => (await http.post<RenderJob>(`${root}/exports/${id}/cancel`)).data,
    retry: async (id: string, key: string) => (await http.post<RenderJob>(`${root}/exports/${id}/retry`, {}, { headers: { 'Idempotency-Key': key } })).data,
    submitRender: async (kind: 'export' | 'preview' | 'retry', body: RenderBody | Record<string, never>, key: string, job_id?: string) => (await http.post<RenderJob>(kind === 'retry' ? `${root}/exports/${job_id}/retry` : `${root}/${kind === 'preview' ? 'previews' : 'exports'}`, body, { headers: { 'Idempotency-Key': key } })).data,
    apply: async (id: string, value: AssemblyState, acknowledge: boolean) => (await http.post<RenderJob>(`${root}/exports/${id}/apply`, { row_version: value.assembly!.row_version, context_hash: value.context_hash, acknowledge_stale_source: acknowledge })).data,
    download: (id: string) => `${http.defaults.baseURL}${root}/exports/${id}/download`,
    downloadCurrent: () => `${http.defaults.baseURL}${root}/current/download`,
  };
}
