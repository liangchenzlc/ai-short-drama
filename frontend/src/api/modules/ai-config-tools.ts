import { http } from '../http';
import type { BeefAPIConnectionDto, ModelTestDto, ProtocolProviderDto } from '../types/ai-config-tools';
import type { AiConfig, ServiceType } from '../../features/ai-config/config-model';

const runtime = '/canvas-runtime';
function checkedTestReceipt(value: ModelTestDto, expectedId?: string): ModelTestDto {
  if (!value || typeof value.id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.id)
    || !['queued', 'running', 'succeeded', 'failed', 'cancelled'].includes(value.status)
    || typeof value.canCancel !== 'boolean' || (expectedId && value.id !== expectedId)) {
    throw new Error('模型测试回执无效，请核对原测试。');
  }
  return value;
}
export const aiConfigTools = {
  async protocols(kind: ServiceType, signal?: AbortSignal) {
    return (await http.get<{ providers: ProtocolProviderDto[] }>(`${runtime}/plugins/catalog`, { params: { scope: 'user.custom-channel', capability: kind }, signal })).data.providers;
  },
  async startTest(config: AiConfig, operationId: string) {
    const profile = config.runtimeProfile;
    const protocol = profile?.protocol ?? ({ text: 'chat-completion', image: 'openai-image', video: 'newapi', audio: 'openai-audio' } as const)[config.serviceType];
    const body = { channel: { id: `host-${config.id}`, name: config.name,
      baseUrl: `/api/v1/canvas-runtime/ai/models/${config.id}`, apiFormat: profile?.api_format ?? 'openai',
      scope: 'system', models: [config.modelKey], modelProfiles: [{ model: config.modelKey, capability: config.serviceType, protocol }] },
      model: config.modelKey, mode: config.serviceType, prompt: config.serviceType === 'text' ? '请只回复 OK。' : 'A simple blue circle on a white background.',
      config: {}, textOptions: { stream: false, thinking: false }, clientOperationId: operationId };
    return checkedTestReceipt((await http.post<ModelTestDto>(`${runtime}/model-tests`, body, { headers: { 'Idempotency-Key': operationId } })).data);
  },
  async readTest(id: string, signal?: AbortSignal) {
    return checkedTestReceipt((await http.get<ModelTestDto>(`${runtime}/model-tests/${encodeURIComponent(id)}`, { signal })).data, id);
  },
  async cancelTest(id: string) {
    return checkedTestReceipt((await http.post<ModelTestDto>(`${runtime}/model-tests/${encodeURIComponent(id)}/cancel`)).data, id);
  },
  async connection(signal?: AbortSignal) {
    return (await http.get<BeefAPIConnectionDto>(`${runtime}/beefapi/connection`, { signal })).data;
  },
  async connect() {
    return (await http.post<BeefAPIConnectionDto>(`${runtime}/beefapi/connection/start`)).data;
  },
  async cancelConnection() {
    return (await http.post<BeefAPIConnectionDto>(`${runtime}/beefapi/connection/cancel`)).data;
  },
  async disconnect() {
    return (await http.post<BeefAPIConnectionDto>(`${runtime}/beefapi/connection/disconnect`)).data;
  },
  async wallet() {
    return (await http.post<{ enterpriseOrigin: string; walletUrl: string }>(`${runtime}/beefapi/connection/open-wallet`)).data;
  },
};
