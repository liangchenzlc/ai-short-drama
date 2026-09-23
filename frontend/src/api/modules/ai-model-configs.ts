import { http } from '../http';
import type { AiModelConfigDto, AiModelConfigCreateDto, AiModelConfigUpdateDto, AiModelConfigListDto, AiModelCapabilitiesDto, ModelDiscoveryDto, ModelDiscoveryRequestDto } from '../types/ai-model-configs';
import type { AiConfig, ConfigDraft, ServiceType } from '../../features/ai-config/config-model';
import { notifyAiConfigsChanged } from '../../features/ai-config/config-events';

const path = '/ai-model-configs';
export function mapAiModelConfig(dto: AiModelConfigDto): AiConfig {
  return {
    id: dto.id, name: dto.name, provider: dto.provider, serviceType: dto.service_type,
    baseUrl: dto.base_url, modelKey: dto.model_key, enabled: dto.enabled === 1,
    isDefault: dto.is_default === 1, hasApiKey: dto.has_api_key, rowVersion: dto.row_version,
  };
}
function draftFields(draft: ConfigDraft): Omit<AiModelConfigCreateDto, 'service_type'> {
  return {
    name: draft.name.trim(), provider: draft.provider.trim(), model_key: draft.modelKey.trim(),
    base_url: draft.baseUrl.trim(), enabled: draft.enabled ? 1 : 0,
    ...(draft.clearApiKey ? { apikey: null } : draft.apiKey ? { apikey: draft.apiKey } : {}),
  };
}
export const aiModelConfigs = {
  async capabilities(id: string, signal?: AbortSignal) {
    return (await http.get<AiModelCapabilitiesDto>(`${path}/${encodeURIComponent(id)}/capabilities`, { signal })).data;
  },
  async discoverModels(body: ModelDiscoveryRequestDto, signal?: AbortSignal) {
    return (await http.post<ModelDiscoveryDto>(`${path}/discover-models`, body, { signal, timeout: 20_000 })).data;
  },
  async list(serviceType: ServiceType, offset: number, limit: number, signal?: AbortSignal) {
    const { data } = await http.get<AiModelConfigListDto>(path, { params: { service_type: serviceType, offset, limit }, signal });
    return { ...data, items: data.items.map(mapAiModelConfig) };
  },
  async get(id: string) { return mapAiModelConfig((await http.get<AiModelConfigDto>(`${path}/${id}`)).data); },
  async create(draft: ConfigDraft) {
    const body: AiModelConfigCreateDto = { ...draftFields(draft), service_type: draft.serviceType };
    const result = mapAiModelConfig((await http.post<AiModelConfigDto>(path, body)).data);
    notifyAiConfigsChanged();
    return result;
  },
  async update(id: string, rowVersion: string, draft: ConfigDraft) {
    const body: AiModelConfigUpdateDto = { ...draftFields(draft), row_version: rowVersion };
    const result = mapAiModelConfig((await http.patch<AiModelConfigDto>(`${path}/${id}`, body)).data);
    notifyAiConfigsChanged();
    return result;
  },
  async remove(id: string, rowVersion: string) {
    await http.delete(`${path}/${id}`, { params: { row_version: rowVersion } });
    notifyAiConfigsChanged();
  },
  async setDefault(id: string, rowVersion: string) {
    const result = mapAiModelConfig((await http.put<AiModelConfigDto>(`${path}/${id}/default`, { row_version: rowVersion })).data);
    notifyAiConfigsChanged();
    return result;
  },
};
