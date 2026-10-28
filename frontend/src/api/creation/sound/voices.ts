import { http } from '@host/api/http';
import type { CharacterVoice, NativeDialogue, SoundMode } from '@host/types/creation/sound/voices';
export const nativeVoiceApi = {
  enabled: async () => (await http.get<{ enabled: boolean }>('/native-voice/capabilities')).data.enabled,
  mode: async (project: string) => (await http.get<SoundMode>(`/projects/${project}/sound-mode`)).data,
  setMode: async (project: string, body: SoundMode) =>
    (await http.put<SoundMode>(`/projects/${project}/sound-mode`, body)).data,
  voices: async (project: string, character: string, signal?: AbortSignal) =>
    (await http.get<CharacterVoice>(`/projects/${project}/characters/${character}/voice`, { signal })).data,
  adopt: async (project: string, character: string, row_version: number, record_id: string) =>
    (
      await http.post<CharacterVoice>(`/projects/${project}/characters/${character}/voice/adopt`, {
        row_version,
        record_id,
      })
    ).data,
  design: async (
    project: string,
    character: string,
    config_id: string,
    voice_prompt: string,
    preview_text: string,
    key: string,
  ) =>
    (
      await http.post<{ generation_id: string }>(
        '/ai/generations/audio',
        {
          config_id,
          source: {
            scene: 'character_voice_design',
            project_id: project,
            asset_id: character,
            voice_prompt,
            preview_text,
          },
        },
        { headers: { 'Idempotency-Key': key } },
      )
    ).data,
  dialogue: async (project: string, shot: string, signal?: AbortSignal) =>
    (await http.get<NativeDialogue>(`/projects/${project}/shots/${shot}/dialogue`, { signal })).data,
  saveDialogue: async (
    project: string,
    shot: string,
    body: {
      row_version: number;
      document: NativeDialogue['document'];
      request_id: string;
    },
  ) => (await http.put<NativeDialogue>(`/projects/${project}/shots/${shot}/dialogue`, body)).data,
};
