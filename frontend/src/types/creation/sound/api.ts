import type {
  AudioCandidate,
  Extraction,
  SoundDocument,
  SoundState,
  Subtitle,
} from '@host/types/creation/sound/tracks';

export interface SoundSaveRequest {
  row_version: string;
  timeline_hash: string;
  document: SoundDocument;
  request_id: string;
  reviewed: boolean;
}

export interface SoundApi {
  enabled: () => Promise<boolean>;
  get: () => Promise<SoundState>;
  nativeSubtitles: () => Promise<{ subtitles: Subtitle[]; timeline_hash: string; notice: string }>;
  save: (body: SoundSaveRequest) => Promise<SoundState>;
  voices: (body: SoundState['voice_defaults']) => Promise<SoundState['voice_defaults']>;
  candidates: (id: string, signal?: AbortSignal) => Promise<AudioCandidate[]>;
  adopt: (rowVersion: string, lineId: string, mediaId: string) => Promise<SoundState>;
  extraction: (id: string, signal?: AbortSignal) => Promise<Extraction>;
  importSrt: (text: string) => Promise<Subtitle[]>;
  srtUrl: string;
  upload: (file: File) => Promise<{ media_id: string; duration_ms: number; url: string }>;
}
