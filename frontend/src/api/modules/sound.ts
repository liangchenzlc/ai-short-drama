import { http } from '../http';
export interface Dialogue { id: string; character: string; text: string; voice: string; config_id: string | null; start_ms: number; media_id: string | null; adopted_hash: string | null }
export interface Subtitle { start_ms: number; end_ms: number; text: string }
export interface Music { media_id: string; start_ms: number; trim_in_ms: number; trim_out_ms: number; volume: number; loop: boolean; fade_in_ms: number; fade_out_ms: number; ducking: boolean }
export interface SoundDocument { dialogue: Dialogue[]; subtitles: Subtitle[]; native_ducking?: Subtitle[]; music: Music | null; original_volume: number; dialogue_volume: number; burn_subtitles: boolean; font_size: number }
export interface SoundState { mode?: 'native' | 'legacy'; row_version: string; timeline_hash: string; duration_ms: number; needs_review: boolean; stale_lines: string[]; document: SoundDocument; media: Record<string, { url: string; duration_ms: number }>; uploads: { media_id: string; name: string; url: string; duration_ms: number }[]; voice_defaults: { row_version: string; voices: Record<string, string> } }
export interface AudioCandidate { id: string; status: string; can_resume: boolean; error?: { code: string; message: string }; line_hash: string; outputs: { media_id: string; url: string; duration_ms: number }[] }
export interface Extraction { status: string; raw_text: string; error?: string; dialogue: Dialogue[] | null }
export function soundApi(project: string, episode: string) {
  const path = `/projects/${project}/episodes/${episode}/sound`;
  return {
    enabled: async () => (await http.get<{ enabled: boolean }>(`${path}/capabilities`)).data.enabled,
    get: async () => (await http.get<SoundState>(path)).data,
    nativeSubtitles: async () => (await http.get<{ subtitles: Subtitle[]; timeline_hash: string; notice: string }>(`${path}/native-subtitles`)).data,
    save: async (body: { row_version: string; timeline_hash: string; document: SoundDocument; request_id: string; reviewed: boolean }) => (await http.put<SoundState>(path, body)).data,
    voices: async (body: SoundState['voice_defaults']) => (await http.put<SoundState['voice_defaults']>(`${path}/voices`, body)).data,
    candidates: async (id: string, signal?: AbortSignal) => (await http.get<AudioCandidate[]>(`${path}/candidates/${encodeURIComponent(id)}`, { signal })).data,
    adopt: async (row_version: string, line_id: string, media_id: string) => (await http.post<SoundState>(`${path}/adopt`, { row_version, line_id, media_id })).data,
    extraction: async (id: string, signal?: AbortSignal) => (await http.get<Extraction>(`${path}/extractions/${id}`, { signal })).data,
    importSrt: async (text: string) => (await http.post<{ subtitles: Subtitle[] }>(`${path}/subtitles/import`, { text })).data.subtitles,
    srtUrl: `${http.defaults.baseURL}${path}/subtitles.srt`,
    upload: async (file: File) => { const body = new FormData(); body.append('file', file); return (await http.post<{ media_id: string; duration_ms: number; url: string }>(`${path}/music`, body, { timeout: 180000 })).data; },
  };
}
