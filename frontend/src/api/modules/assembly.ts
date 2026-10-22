import { http } from '../http';
import type { SoundState } from './sound';

export interface FrameTransform { fit: 'contain' | 'cover'; scale: number; x: number; y: number }
export interface AssemblyMaterial { media_id: string; name: string; kind: 'image' | 'video' | 'audio'; duration_ms: number | null; width: number | null; height: number | null; url: string; poster: string | null; has_audio: boolean }
export interface MediaJob { id: string; kind: 'transcription' | 'audio_analysis'; status: string; stage: string; progress: number; can_cancel: boolean; can_retry: boolean; error: { message: string } | null; result: { subtitles?: import('./sound').Subtitle[]; start_ms?: number; end_ms?: number; source_hash?: string; voice_context_hash?: string; ranges?: { clip_id: string; start_ms: number; end_ms: number; source_in_ms: number }[]; waveform?: number[]; gain_db?: number; integrated_lufs?: number | null; submitted?: boolean; reconciled?: boolean; reconciled_at?: string } | null }

export interface AssemblyClip {
  filmstrip?: { url: string | null; count: number; interval_ms: number } | null;
  source_clip_id?: string;
  id: string; shot_id: string | null; media_id: string | null; position: number; shot_position: number;
  source_type?: 'shot' | 'video' | 'image'; display_duration_ms?: number | null; transform?: FrameTransform | null; has_audio?: boolean; width?: number | null; height?: number | null;
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
  aspect?: string; resolution?: string; output_width?: number | null; output_height?: number | null; output_source?: { media_id: string; clip_id: string } | null;
  timeline?: { clip_id: string; shot_id: string | null; trim_in_ms: number; trim_out_ms: number; muted: boolean;
    media_id?: string | null; duration_ms?: number | null; url?: string | null; poster?: string | null;
    filmstrip?: AssemblyClip['filmstrip']; }[];
}
export interface AssemblyState {
  editor_version?: 2; sound?: SoundState; subtitle_font?: string; voice_context_hash?: string; materials?: AssemblyMaterial[];
  assembly: { id: string; row_version: string; aspect: 'auto' | '16:9' | '9:16'; resolution: '720p' | '1080p'; output_width?: number | null; output_height?: number | null; output_status?: 'ready' | 'preparing' | 'invalid'; output_source?: { media_id: string; clip_id: string; width?: number; height?: number } | null; current_media_id: string | null } | null;
  source_hash: string; source_count?: number; context_hash?: string; clips?: AssemblyClip[];
  sources?: AssemblyClip[];
  changes?: { shot_id: string; position: number; kind: 'added' | 'replacement' | 'archived' }[]; jobs?: RenderJob[];
  current_work?: AssemblyCurrentWork | null;
}
export interface AssemblyCurrentWork {
  media_id: string; url: string | null; width: number | null; height: number | null;
  duration_ms: number | null; is_stale: boolean;
}
export const editBody = (value: AssemblyState) => ({ row_version: value.assembly!.row_version, aspect: value.assembly!.aspect, resolution: value.assembly!.resolution,
  clips: (value.clips ?? []).map(({ id, source_clip_id, included, muted, trim_in_ms, trim_out_ms }) => ({ id, source_clip_id, included, muted, trim_in_ms, trim_out_ms })) });
export interface RenderBody { row_version: string; source_hash: string; acknowledge_stale_source: boolean }
export const renderBody = (value: AssemblyState, acknowledge: boolean): RenderBody => ({ row_version: value.assembly!.row_version, source_hash: value.source_hash, acknowledge_stale_source: acknowledge });
export function assemblyApi(projectId: string) {
  const root = `/projects/${encodeURIComponent(projectId)}/assembly`;
  let saveRequest: { fingerprint: string; request_id: string } | null = null;
  return {
    mediaScope: root,
    get: async (signal?: AbortSignal, materialIds: string[] = []) => (await http.get<AssemblyState>(`${root}/editor`, { signal, params: materialIds.length ? { material_ids: [...new Set(materialIds)].slice(0, 900).join(',') } : undefined })).data,
    initialize: async () => { await http.post(`${root}/initialize`); return (await http.get<AssemblyState>(`${root}/editor`)).data; },
    save: async (value: AssemblyState) => {
      const body = value.editor_version === 2 && value.sound ? {
        schema_version: 2, row_version: value.assembly!.row_version, sound_row_version: value.sound.row_version,
        aspect: value.assembly!.aspect, resolution: value.assembly!.resolution, sound: value.sound.document,
        clips: (value.clips ?? []).map(c => ({ id: c.id, source_clip_id: c.source_clip_id, media_id: c.media_id, source_type: c.source_type ?? 'shot', included: c.included, muted: c.muted, trim_in_ms: c.trim_in_ms, trim_out_ms: c.trim_out_ms, display_duration_ms: c.display_duration_ms ?? null, transform: c.transform ?? { fit: 'contain', scale: 1, x: .5, y: .5 } })),
      } : editBody(value), fingerprint = JSON.stringify(body);
      if (saveRequest?.fingerprint !== fingerprint) saveRequest = { fingerprint, request_id: crypto.randomUUID() };
      const saved = (await http.request<AssemblyState>({ method: value.editor_version === 2 ? 'PUT' : 'PATCH', url: value.editor_version === 2 ? `${root}/editor` : root, data: { ...body, request_id: saveRequest.request_id } })).data;
      saveRequest = null;
      return saved;
    },
    sync: async (value: AssemblyState) => { await http.post(`${root}/sync`, { row_version: value.assembly!.row_version, source_hash: value.source_hash }); return (await http.get<AssemblyState>(`${root}/editor`)).data; },
    materials: async () => (await http.get<{ items: AssemblyMaterial[] }>(`${root}/materials`)).data.items,
    importMaterial: async (media_id: string) => (await http.post<AssemblyMaterial>(`${root}/materials/import`, { media_id })).data,
    uploadMaterial: async (file: File) => { const data = new FormData(); data.append('file', file); return (await http.post<AssemblyMaterial>(`${root}/materials/upload`, data, { timeout: 180000 })).data; },
    transcriptionModels: async () => (await http.get<{ local_available: boolean; models: { id: string; name: string }[] }>(`${root}/transcription-models`)).data,
    transcribe: async (body: { row_version: string; clip_id?: string; language: string; model_config_id?: string }, key: string) => (await http.post<MediaJob>(`${root}/transcriptions`, body, { headers: { 'Idempotency-Key': key } })).data,
    analyse: async (body: { media_id: string; trim_in_ms: number; trim_out_ms: number }, key: string) => (await http.post<MediaJob>(`${root}/audio-analysis`, body, { headers: { 'Idempotency-Key': key } })).data,
    mediaJob: async (id: string, signal?: AbortSignal) => (await http.get<MediaJob>(`${root}/media-jobs/${id}`, { signal })).data,
    mediaJobs: async () => (await http.get<{ items: MediaJob[] }>(`${root}/media-jobs`)).data.items,
    cancelMediaJob: async (id: string) => (await http.post<MediaJob>(`${root}/media-jobs/${id}/cancel`)).data,
    acknowledgeMediaJob: async (id: string) => (await http.post<MediaJob>(`${root}/media-jobs/${id}/acknowledge`)).data,
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
