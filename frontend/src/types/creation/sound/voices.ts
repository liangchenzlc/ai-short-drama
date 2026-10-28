import type { SafeTaskError } from '@host/types/generations/api';

export interface VoiceCandidate {
  record_id: string;
  task_id: string;
  status: string;
  can_resume: boolean;
  error?: SafeTaskError;
  voice_id: string | null;
  description: string;
  preview_text: string;
  media_id: string | null;
  duration_ms: number | null;
  url: string | null;
  adoptable: boolean;
}

export interface CharacterVoice {
  row_version: number;
  record_id: string | null;
  candidates: VoiceCandidate[];
  current_voice?: {
    media_id: string;
    url: string | null;
    duration_ms: number | null;
    row_version: number;
  } | null;
}

export interface SoundMode {
  mode: 'legacy' | 'native';
  row_version: number;
}

export interface NativeLine {
  character_id: string;
  text: string;
  delivery: string;
  speech: 'onscreen' | 'voiceover';
}

export interface NativeDialogue {
  row_version: number;
  mode: SoundMode['mode'];
  document: { lines: NativeLine[]; reviewed: boolean };
  characters: { id: string; name: string }[];
  voices: {
    character_id: string;
    name: string;
    version: number;
    media_id: string | null;
    duration_ms: number | null;
    url: string | null;
  }[];
}
