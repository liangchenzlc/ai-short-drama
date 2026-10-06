export type ServiceTypeDto = 'text' | 'image' | 'video' | 'audio';
export type ConfigJson = null | boolean | number | string | ConfigJson[] | { [key: string]: ConfigJson };
export type ConfigJsonObject = { [key: string]: ConfigJson };
export interface AiRuntimeProfileDto {
  version: 1;
  api_format: 'openai' | 'gemini' | 'claude';
  protocol: string;
  reference_asset_origin?: string | null;
  capability_config?: ConfigJsonObject | null;
  default_options?: ConfigJsonObject | null;
  logical_capability_spec?: ConfigJsonObject | null;
  logical_capability_profiles?: ConfigJsonObject[] | null;
  video_capabilities_version?: string | null;
  concurrency_limit?: number | null;
}
export interface ConfigHeaderReadDto { name: string; has_value: boolean }
export interface ConfigHeaderWriteDto { name: string; value: string }
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
  has_secret_key: boolean;
  headers: ConfigHeaderReadDto[];
  credential_source: 'manual' | 'beefapi';
  runtime_profile: AiRuntimeProfileDto | null;
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
  secret_key?: string | null;
  headers?: ConfigHeaderWriteDto[];
  runtime_profile?: AiRuntimeProfileDto | null;
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
