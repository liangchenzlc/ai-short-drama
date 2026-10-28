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
  rowVersion: string;
  isDefault: boolean;
}

export interface RuntimeDraft {
  enabled: boolean;
  apiFormat: 'openai' | 'gemini' | 'claude';
  protocol: string;
  referenceAssetOrigin: string;
  capabilityConfig: string;
  defaultOptions: string;
  logicalCapabilitySpec: string;
  logicalCapabilityProfiles: string;
  videoCapabilitiesVersion: string;
  concurrencyLimit: string;
  runningHubKind: 'workflow' | 'app';
  runningHubId: string;
  runningHubTitle: string;
  runningHubFields: string;
  runningHubJson: string;
}

export interface HeaderDraft {
  name: string;
  value: string;
  hasValue: boolean;
}

export type ConfigDraft = Pick<AiConfig, 'name' | 'provider' | 'serviceType' | 'baseUrl' | 'modelKey' | 'enabled'> & {
  apiKey: string;
  clearApiKey: boolean;
  secretKey: string;
  clearSecretKey: boolean;
  headers: HeaderDraft[];
  headersChanged: boolean;
  runtime: RuntimeDraft;
};
