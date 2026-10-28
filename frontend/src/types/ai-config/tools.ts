import type { AiRuntimeProfileDto, ConfigJsonObject, ServiceTypeDto } from '@host/types/ai-config/api';
export interface ModelTestRequestDto {
  channel: {
    id: string;
    name: string;
    baseUrl: string;
    apiFormat: AiRuntimeProfileDto['api_format'];
    scope: 'system';
    models: string[];
    modelProfiles: {
      model: string;
      capability: ServiceTypeDto;
      protocol: string;
    }[];
  };
  model: string;
  mode: ServiceTypeDto;
  prompt: string;
  config: ConfigJsonObject;
  textOptions: { stream: boolean; thinking: boolean };
  clientOperationId: string;
}

export interface ProtocolProviderDto {
  id: string;
  name: string;
  categories: ServiceTypeDto[];
  enabled: boolean;
  unavailableReason?: string;
  create?: string;
}
export interface ModelTestDto {
  id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  result?: {
    mode?: string;
    text?: string;
    images?: { url?: string; dataUrl: string }[];
    video?: { url?: string; dataUrl: string };
    audio?: { url?: string; dataUrl: string };
  };
  error?: string;
  errorCode?: string;
  canCancel: boolean;
}
