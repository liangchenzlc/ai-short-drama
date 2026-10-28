import { http } from '@host/api/http';
import type {
  AssemblyMaterial,
  AssemblyState,
  MediaJob,
  RenderBody,
  RenderJob,
} from '@host/types/creation/assembly/document';
export const renderBody = (value: AssemblyState, acknowledge: boolean): RenderBody => ({
  row_version: value.assembly!.row_version,
  source_hash: value.source_hash,
  acknowledge_stale_source: acknowledge,
});
export function assemblyApi(projectId: string) {
  const root = `/projects/${encodeURIComponent(projectId)}/assembly`;
  let saveRequest: { fingerprint: string; request_id: string } | null = null;
  return {
    mediaScope: root,
    get: async (signal?: AbortSignal, materialIds: string[] = []) =>
      (
        await http.get<AssemblyState>(`${root}/editor`, {
          signal,
          params: materialIds.length
            ? {
                material_ids: [...new Set(materialIds)].slice(0, 900).join(','),
              }
            : undefined,
        })
      ).data,
    initialize: async () => {
      await http.post(`${root}/initialize`);
      return (await http.get<AssemblyState>(`${root}/editor`)).data;
    },
    save: async (value: AssemblyState) => {
      const body = {
        schema_version: 2,
        row_version: value.assembly!.row_version,
        sound_row_version: value.sound!.row_version,
        aspect: value.assembly!.aspect,
        resolution: value.assembly!.resolution,
        sound: value.sound!.document,
        clips: (value.clips ?? []).map((c) => ({
          id: c.id,
          source_clip_id: c.source_clip_id,
          media_id: c.media_id,
          source_type: c.source_type ?? 'shot',
          included: c.included,
          muted: c.muted,
          trim_in_ms: c.trim_in_ms,
          trim_out_ms: c.trim_out_ms,
          display_duration_ms: c.display_duration_ms ?? null,
          transform: c.transform ?? { fit: 'contain', scale: 1, x: 0.5, y: 0.5 },
        })),
      };
      const fingerprint = JSON.stringify(body);
      if (saveRequest?.fingerprint !== fingerprint) saveRequest = { fingerprint, request_id: crypto.randomUUID() };
      const saved = (
        await http.request<AssemblyState>({
          method: 'PUT',
          url: `${root}/editor`,
          data: { ...body, request_id: saveRequest.request_id },
        })
      ).data;
      saveRequest = null;
      return saved;
    },
    sync: async (value: AssemblyState) => {
      await http.post(`${root}/sync`, {
        row_version: value.assembly!.row_version,
        source_hash: value.source_hash,
      });
      return (await http.get<AssemblyState>(`${root}/editor`)).data;
    },
    materials: async () => (await http.get<{ items: AssemblyMaterial[] }>(`${root}/materials`)).data.items,
    importMaterial: async (media_id: string) =>
      (
        await http.post<AssemblyMaterial>(`${root}/materials/import`, {
          media_id,
        })
      ).data,
    uploadMaterial: async (file: File) => {
      const data = new FormData();
      data.append('file', file);
      return (
        await http.post<AssemblyMaterial>(`${root}/materials/upload`, data, {
          timeout: 180000,
        })
      ).data;
    },
    transcriptionModels: async () =>
      (
        await http.get<{
          local_available: boolean;
          models: { id: string; name: string }[];
        }>(`${root}/transcription-models`)
      ).data,
    transcribe: async (
      body: {
        row_version: string;
        clip_id?: string;
        language: string;
        model_config_id?: string;
      },
      key: string,
    ) =>
      (
        await http.post<MediaJob>(`${root}/transcriptions`, body, {
          headers: { 'Idempotency-Key': key },
        })
      ).data,
    analyse: async (body: { media_id: string; trim_in_ms: number; trim_out_ms: number }, key: string) =>
      (
        await http.post<MediaJob>(`${root}/audio-analysis`, body, {
          headers: { 'Idempotency-Key': key },
        })
      ).data,
    mediaJob: async (id: string, signal?: AbortSignal) =>
      (await http.get<MediaJob>(`${root}/media-jobs/${id}`, { signal })).data,
    mediaJobs: async () => (await http.get<{ items: MediaJob[] }>(`${root}/media-jobs`)).data.items,
    cancelMediaJob: async (id: string) => (await http.post<MediaJob>(`${root}/media-jobs/${id}/cancel`)).data,
    acknowledgeMediaJob: async (id: string) => (await http.post<MediaJob>(`${root}/media-jobs/${id}/acknowledge`)).data,
    export: async (value: AssemblyState, acknowledge: boolean, key: string) =>
      (
        await http.post<RenderJob>(
          `${root}/exports`,
          {
            row_version: value.assembly!.row_version,
            source_hash: value.source_hash,
            acknowledge_stale_source: acknowledge,
          },
          { headers: { 'Idempotency-Key': key } },
        )
      ).data,
    preview: async (value: AssemblyState, acknowledge: boolean, key: string) =>
      (
        await http.post<RenderJob>(
          `${root}/previews`,
          {
            row_version: value.assembly!.row_version,
            source_hash: value.source_hash,
            acknowledge_stale_source: acknowledge,
          },
          { headers: { 'Idempotency-Key': key } },
        )
      ).data,
    job: async (id: string, signal?: AbortSignal) =>
      (await http.get<RenderJob>(`${root}/exports/${id}`, { signal })).data,
    history: async (offset = 0) =>
      (await http.get<{ items: RenderJob[]; has_more: boolean }>(`${root}/exports`, { params: { offset, limit: 20 } }))
        .data,
    cancel: async (id: string) => (await http.post<RenderJob>(`${root}/exports/${id}/cancel`)).data,
    retry: async (id: string, key: string) =>
      (await http.post<RenderJob>(`${root}/exports/${id}/retry`, {}, { headers: { 'Idempotency-Key': key } })).data,
    submitRender: async (
      kind: 'export' | 'preview' | 'retry',
      body: RenderBody | Record<string, never>,
      key: string,
      job_id?: string,
    ) =>
      (
        await http.post<RenderJob>(
          kind === 'retry'
            ? `${root}/exports/${job_id}/retry`
            : `${root}/${kind === 'preview' ? 'previews' : 'exports'}`,
          body,
          { headers: { 'Idempotency-Key': key } },
        )
      ).data,
    apply: async (id: string, value: AssemblyState, acknowledge: boolean) =>
      (
        await http.post<RenderJob>(`${root}/exports/${id}/apply`, {
          row_version: value.assembly!.row_version,
          context_hash: value.context_hash,
          acknowledge_stale_source: acknowledge,
        })
      ).data,
    download: (id: string) => `${http.defaults.baseURL}${root}/exports/${id}/download`,
    downloadCurrent: () => `${http.defaults.baseURL}${root}/current/download`,
  };
}
