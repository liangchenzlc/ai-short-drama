import type { AiRuntimeProfileDto, ConfigHeaderReadDto } from '../../api/types/ai-model-configs';

export type ServiceType = "text" | "image" | "video" | "audio";
export const serviceLabels: Record<ServiceType, string> = {
  audio: "配音模型",
  text: "文本模型",
  image: "生图模型",
  video: "生视频模型",
};
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
}
export interface HeaderDraft { name: string; value: string; hasValue: boolean }
export type ConfigDraft = Pick<AiConfig, 'name' | 'provider' | 'serviceType' | 'baseUrl' | 'modelKey' | 'enabled'> & {
  apiKey: string;
  clearApiKey: boolean;
  secretKey: string;
  clearSecretKey: boolean;
  headers: HeaderDraft[];
  headersChanged: boolean;
  runtime: RuntimeDraft;
};
export const emptyRuntime: RuntimeDraft = {
  enabled: false, apiFormat: 'openai', protocol: '', referenceAssetOrigin: '',
  capabilityConfig: '', defaultOptions: '', logicalCapabilitySpec: '',
  logicalCapabilityProfiles: '', videoCapabilitiesVersion: '', concurrencyLimit: '',
};
export const emptyConfig: ConfigDraft = {
  name: "",
  provider: "",
  serviceType: "text",
  baseUrl: "",
  modelKey: "",
  enabled: true,
  clearApiKey: false,
  apiKey: "",
  secretKey: '', clearSecretKey: false, headers: [], headersChanged: false,
  runtime: emptyRuntime,
};
