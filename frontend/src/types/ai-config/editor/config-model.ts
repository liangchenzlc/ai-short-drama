import type { AiRuntimeProfileDto, ConfigHeaderReadDto } from '@host/types/ai-config/api';

export type ServiceType = 'text' | 'image' | 'video' | 'audio';

export interface AiConfig {
  id: string;
  name: string;
  provider: string;
  serviceType: ServiceType;
  baseUrl: string;
  modelKey: string;
  enabled: boolean;
  hasApiKey: boolean;
  hasSecretKey: boolean;
  headers: ConfigHeaderReadDto[];
  credentialSource: 'manual' | 'beefapi';
  runtimeProfile: AiRuntimeProfileDto | null;
  interfaceType: string;
  rowVersion: string;
  isDefault: boolean;
}

export interface RuntimeDraft {
  apiFormat: 'auto' | 'openai' | 'gemini' | 'claude';
  protocol: string;
}

export type ConfigDraft = Pick<AiConfig, 'name' | 'provider' | 'serviceType' | 'baseUrl' | 'modelKey' | 'enabled'> & {
  apiKey: string;
  clearApiKey: boolean;
  secretKey: string;
  clearSecretKey: boolean;
  runtime: RuntimeDraft;
};
