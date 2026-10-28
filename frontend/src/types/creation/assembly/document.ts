import type { SoundState } from '@host/types/creation/sound/tracks';

export interface FrameTransform {
  fit: 'contain' | 'cover';
  scale: number;
  x: number;
  y: number;
}

export interface AssemblyMaterial {
  media_id: string;
  name: string;
  kind: 'image' | 'video' | 'audio';
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  url: string;
  poster: string | null;
  has_audio: boolean;
}

export interface MediaJob {
  id: string;
  kind: 'transcription' | 'audio_analysis';
  status: string;
  stage: string;
  progress: number;
  can_cancel: boolean;
  can_retry: boolean;
  error: { message: string } | null;
  result: {
    subtitles?: import('../sound/tracks').Subtitle[];
    start_ms?: number;
    end_ms?: number;
    source_hash?: string;
    voice_context_hash?: string;
    ranges?: {
      clip_id: string;
      start_ms: number;
      end_ms: number;
      source_in_ms: number;
    }[];
    waveform?: number[];
    gain_db?: number;
    integrated_lufs?: number | null;
    submitted?: boolean;
    reconciled?: boolean;
    reconciled_at?: string;
  } | null;
}

export interface AssemblyClip {
  filmstrip?: { url: string | null; count: number; interval_ms: number } | null;
  source_clip_id?: string;
  id: string;
  shot_id: string | null;
  media_id: string | null;
  position: number;
  shot_position: number;
  source_type?: 'shot' | 'video' | 'image';
  display_duration_ms?: number | null;
  transform?: FrameTransform | null;
  has_audio?: boolean;
  width?: number | null;
  height?: number | null;
  included: boolean;
  muted: boolean;
  trim_in_ms: number;
  trim_out_ms: number | null;
  duration_ms: number | null;
  script: string;
  url: string | null;
  poster: string | null;
  is_stale: boolean;
  archived: boolean;
  issue: 'missing' | 'invalid' | 'preparing' | 'trim' | null;
}

export interface RenderJob {
  can_cancel?: boolean;
  can_retry?: boolean;
  initiated_by?: string | null;
  id: string;
  kind: 'probe' | 'export' | 'preview';
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  stage: string;
  progress: number;
  cancel_requested: boolean;
  error: { message: string } | null;
  created_at: string;
  finished_at: string | null;
  context_hash: string;
  url: string | null;
  media_id: string | null;
  duration_ms: number | null;
  is_stale: boolean;
  aspect?: string;
  resolution?: string;
  output_width?: number | null;
  output_height?: number | null;
  output_source?: { media_id: string; clip_id: string } | null;
  timeline?: {
    clip_id: string;
    shot_id: string | null;
    trim_in_ms: number;
    trim_out_ms: number;
    muted: boolean;
    media_id?: string | null;
    duration_ms?: number | null;
    url?: string | null;
    poster?: string | null;
    filmstrip?: AssemblyClip['filmstrip'];
  }[];
}

export interface AssemblyState {
  sound?: SoundState;
  subtitle_font?: string;
  voice_context_hash?: string;
  materials?: AssemblyMaterial[];
  assembly: {
    id: string;
    row_version: string;
    aspect: 'auto' | '16:9' | '9:16';
    resolution: '720p' | '1080p';
    output_width?: number | null;
    output_height?: number | null;
    output_status?: 'ready' | 'preparing' | 'invalid';
    output_source?: {
      media_id: string;
      clip_id: string;
      width?: number;
      height?: number;
    } | null;
    current_media_id: string | null;
  } | null;
  source_hash: string;
  source_count?: number;
  context_hash?: string;
  clips?: AssemblyClip[];
  sources?: AssemblyClip[];
  changes?: {
    shot_id: string;
    position: number;
    kind: 'added' | 'replacement' | 'archived';
  }[];
  jobs?: RenderJob[];
  current_work?: AssemblyCurrentWork | null;
}

export interface AssemblyCurrentWork {
  media_id: string;
  url: string | null;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  is_stale: boolean;
}

export interface RenderBody {
  row_version: string;
  source_hash: string;
  acknowledge_stale_source: boolean;
}
