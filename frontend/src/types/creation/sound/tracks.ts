export interface Dialogue {
  id: string;
  character: string;
  text: string;
  voice: string;
  config_id: string | null;
  start_ms: number;
  media_id: string | null;
  adopted_hash: string | null;
}

export interface Subtitle {
  id?: string | null;
  start_ms: number;
  end_ms: number;
  text: string;
  anchor_clip_id?: string | null;
  anchor_source_in_ms?: number | null;
}

export type AudioKind = 'original' | 'dialogue' | 'music' | 'effect';

export interface AudioClip extends Music {
  id: string;
  dialogue_id?: string | null;
  loop_offset_ms?: number;
  kind: AudioKind;
  name: string;
  duration_ms: number;
  anchor_clip_id?: string | null;
  anchor_source_in_ms?: number | null;
  muted: boolean;
  gain_db: number;
}

export interface SoundTrack {
  id: AudioKind | 'subtitles';
  muted: boolean;
  locked: boolean;
}

export interface SubtitleStyle {
  color: string;
  outline_color: string;
  outline_width: number;
  background: boolean;
  background_color: string;
  background_opacity: number;
  alignment: number;
  margin_percent: number;
  bold: boolean;
}

export interface Music {
  media_id: string;
  start_ms: number;
  trim_in_ms: number;
  trim_out_ms: number;
  volume: number;
  loop: boolean;
  fade_in_ms: number;
  fade_out_ms: number;
  ducking: boolean;
}

export interface SoundDocument {
  schema_version?: 1 | 2;
  picture_locked?: boolean;
  audio_clips?: AudioClip[];
  tracks?: SoundTrack[];
  subtitle_style?: SubtitleStyle;
  dialogue: Dialogue[];
  subtitles: Subtitle[];
  native_ducking?: Subtitle[];
  music: Music | null;
  original_volume: number;
  dialogue_volume: number;
  burn_subtitles: boolean;
  font_size: number;
}

export interface SoundState {
  mode?: 'native' | 'legacy';
  row_version: string;
  timeline_hash: string;
  duration_ms: number;
  needs_review: boolean;
  stale_lines: string[];
  document: SoundDocument;
  media: Record<string, { url: string; duration_ms: number }>;
  uploads: {
    media_id: string;
    name: string;
    url: string;
    duration_ms: number;
  }[];
  voice_defaults: { row_version: string; voices: Record<string, string> };
}

export interface AudioCandidate {
  id: string;
  status: string;
  can_resume: boolean;
  error?: { code: string; message: string };
  line_hash: string;
  outputs: { media_id: string; url: string; duration_ms: number }[];
}

export interface Extraction {
  status: string;
  raw_text: string;
  error?: string;
  dialogue: Dialogue[] | null;
}
