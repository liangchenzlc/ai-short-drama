export type ServiceTypeDto = 'text' | 'image' | 'video' | 'audio';
export interface AiModelConfigDto {
  id: string;
  service_type: ServiceTypeDto;
  name: string;
  provider: string;
  model_key: string;
  base_url: string;
  enabled: 0 | 1;
  is_deleted: 0 | 1;
  is_default: 0 | 1;
  row_version: string;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
}
export interface AiModelConfigCreateDto {
  service_type: ServiceTypeDto;
  name: string;
  provider: string;
  model_key: string;
  base_url: string;
  apikey?: string | null;
  enabled: 0 | 1;
}
export type AiModelConfigUpdateDto = Partial<Omit<AiModelConfigCreateDto, 'service_type'>> & { row_version: string };
export interface AiModelConfigListDto { items: AiModelConfigDto[]; total: number; offset: number; limit: number }
export interface ModelDiscoveryRequestDto { base_url: string; apikey?: string | null; config_id?: string }
export interface ModelDiscoveryDto { items: { id: string }[]; truncated: boolean }
export interface AiModelCapabilitiesDto {
  known: boolean;
  parameters: string[];
  reference_images: boolean;
  first_frame: boolean;
  last_frame: boolean;
  video_input?: { first_frame: boolean; reference_images?: boolean; duration_seconds: number[] | null; resolutions: string[] | null; audio_references?: boolean; generate_audio?: boolean; audio_evidence?: string; voice_fidelity?: string };
}
