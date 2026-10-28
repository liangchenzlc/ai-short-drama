import { http } from '@host/api/http';
import type { SoundApi, SoundSaveRequest } from '@host/types/creation/sound/api';
import type { AudioCandidate, Extraction, SoundState, Subtitle } from '@host/types/creation/sound/tracks';
export function soundApi(project: string): SoundApi {
  const path = `/projects/${project}/sound`;
  return {
    enabled: async () => (await http.get<{ enabled: boolean }>(`${path}/capabilities`)).data.enabled,
    get: async () => (await http.get<SoundState>(path)).data,
    nativeSubtitles: async () =>
      (
        await http.get<{
          subtitles: Subtitle[];
          timeline_hash: string;
          notice: string;
        }>(`${path}/native-subtitles`)
      ).data,
    save: async (body: SoundSaveRequest) => (await http.put<SoundState>(path, body)).data,
    voices: async (body: SoundState['voice_defaults']) =>
      (await http.put<SoundState['voice_defaults']>(`${path}/voices`, body)).data,
    candidates: async (id: string, signal?: AbortSignal) =>
      (await http.get<AudioCandidate[]>(`${path}/candidates/${encodeURIComponent(id)}`, { signal })).data,
    adopt: async (row_version: string, line_id: string, media_id: string) =>
      (
        await http.post<SoundState>(`${path}/adopt`, {
          row_version,
          line_id,
          media_id,
        })
      ).data,
    extraction: async (id: string, signal?: AbortSignal) =>
      (await http.get<Extraction>(`${path}/extractions/${id}`, { signal })).data,
    importSrt: async (text: string) =>
      (
        await http.post<{ subtitles: Subtitle[] }>(`${path}/subtitles/import`, {
          text,
        })
      ).data.subtitles,
    srtUrl: `${http.defaults.baseURL}${path}/subtitles.srt`,
    upload: async (file: File) => {
      const body = new FormData();
      body.append('file', file);
      return (
        await http.post<{ media_id: string; duration_ms: number; url: string }>(`${path}/music`, body, {
          timeout: 180000,
        })
      ).data;
    },
  };
}
