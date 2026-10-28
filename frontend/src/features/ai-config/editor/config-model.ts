import type { ConfigDraft, RuntimeDraft, ServiceType } from '@host/types/ai-config/editor/config-model';
export const serviceLabels: Record<ServiceType, string> = {
  audio: '配音模型',
  text: '文本模型',
  image: '生图模型',
  video: '生视频模型',
};
export const emptyRuntime: RuntimeDraft = {
  enabled: false,
  apiFormat: 'openai',
  protocol: '',
  referenceAssetOrigin: '',
  capabilityConfig: '',
  defaultOptions: '',
  logicalCapabilitySpec: '',
  logicalCapabilityProfiles: '',
  videoCapabilitiesVersion: '',
  concurrencyLimit: '',
  runningHubKind: 'workflow',
  runningHubId: '',
  runningHubTitle: '',
  runningHubFields: '',
  runningHubJson: '',
};
export const emptyConfig: ConfigDraft = {
  name: '',
  provider: '',
  serviceType: 'text',
  baseUrl: '',
  modelKey: '',
  enabled: true,
  clearApiKey: false,
  apiKey: '',
  secretKey: '',
  clearSecretKey: false,
  headers: [],
  headersChanged: false,
  runtime: emptyRuntime,
};
