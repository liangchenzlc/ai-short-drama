import { http } from '../http';
import type { SafeTaskError } from '../types/generations';

export interface VoiceCandidate { record_id: string; task_id: string; status: string; can_resume: boolean; error?: SafeTaskError; voice_id: string | null; description: string; preview_text: string; media_id: string | null; duration_ms: number | null; url: string | null; adoptable: boolean }
export interface CharacterVoice {
  row_version: number; record_id: string | null; candidates: VoiceCandidate[];
  current_voice?: { media_id: string; url: string | null; duration_ms: number | null; row_version: number } | null;
}
export interface SoundMode { mode: 'legacy' | 'native'; row_version: number }
export interface NativeLine { character_id: string; text: string; delivery: string; speech: 'onscreen' | 'voiceover' }
export interface NativeDialogue { row_version: number; mode: SoundMode['mode']; document: { lines: NativeLine[]; reviewed: boolean }; characters: { id: string; name: string }[]; voices: { character_id: string; name: string; version: number; media_id: string | null; duration_ms: number | null; url: string | null }[] }
export const nativeVoiceApi = {
  enabled: async () => (await http.get<{ enabled: boolean }>('/native-voice/capabilities')).data.enabled,
  mode: async (project: string) => (await http.get<SoundMode>(`/projects/${project}/sound-mode`)).data,
  setMode: async (project: string, body: SoundMode) => (await http.put<SoundMode>(`/projects/${project}/sound-mode`, body)).data,
  voices: async (project: string, character: string, signal?: AbortSignal) => (await http.get<CharacterVoice>(`/projects/${project}/characters/${character}/voice`, { signal })).data,
  adopt: async (project: string, character: string, row_version: number, record_id: string) => (await http.post<CharacterVoice>(`/projects/${project}/characters/${character}/voice/adopt`, { row_version, record_id })).data,
  design: async (project: string, character: string, config_id: string, voice_prompt: string, preview_text: string, key: string) => (await http.post<{ generation_id: string }>('/ai/generations/audio', { config_id, source: { scene: 'character_voice_design', project_id: project, asset_id: character, voice_prompt, preview_text } }, { headers: { 'Idempotency-Key': key } })).data,
  dialogue: async (project: string, episode: string, shot: string, signal?: AbortSignal) => (await http.get<NativeDialogue>(`/projects/${project}/episodes/${episode}/shots/${shot}/dialogue`, { signal })).data,
  saveDialogue: async (project: string, episode: string, shot: string, body: { row_version: number; document: NativeDialogue['document']; request_id: string }) => (await http.put<NativeDialogue>(`/projects/${project}/episodes/${episode}/shots/${shot}/dialogue`, body)).data,
};
