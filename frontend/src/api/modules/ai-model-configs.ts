import { ApiError, http } from '../http';
import type { AiModelConfigDto, AiModelConfigCreateDto, AiModelConfigUpdateDto, AiModelConfigListDto, AiModelCapabilitiesDto, ModelDiscoveryDto, ModelDiscoveryRequestDto } from '../types/ai-model-configs';
import type { AiConfig, ConfigDraft, ServiceType } from '../../features/ai-config/config-model';
import { notifyAiConfigsChanged } from '../../features/ai-config/config-events';
import { runtimeModelDiscoveryBody, serializeConfigDraft } from '../../features/ai-config/config-form-model';

const path = '/ai-model-configs';
export function mapAiModelConfig(dto: AiModelConfigDto): AiConfig {
  return {
    id: dto.id, name: dto.name, provider: dto.provider, serviceType: dto.service_type,
    baseUrl: dto.base_url, modelKey: dto.model_key, enabled: dto.enabled === 1,
    isDefault: dto.is_default === 1, hasApiKey: dto.has_api_key, rowVersion: dto.row_version,
    hasSecretKey: dto.has_secret_key ?? false, headers: dto.headers ?? [],
    credentialSource: dto.credential_source ?? 'manual', runtimeProfile: dto.runtime_profile ?? null,
  };
}
export const aiModelConfigs = {
  async capabilities(id: string, signal?: AbortSignal) {
    return (await http.get<AiModelCapabilitiesDto>(`${path}/${encodeURIComponent(id)}/capabilities`, { signal })).data;
  },
  async discoverModels(body: ModelDiscoveryRequestDto, signal?: AbortSignal) {
    return (await http.post<ModelDiscoveryDto>(`${path}/discover-models`, body, { signal, timeout: 20_000 })).data;
  },
  async discoverRuntimeModels(draft: ConfigDraft, existing: AiConfig | null, signal?: AbortSignal) {
    let body: ReturnType<typeof runtimeModelDiscoveryBody>;
    try { body = runtimeModelDiscoveryBody(draft, existing); }
    catch (cause) { throw new ApiError(cause instanceof Error ? cause.message : '请检查模型目录参数。', 'MODEL_CONFIG_DRAFT_INVALID'); }
    const { data } = await http.post<{ models: { id: string }[] }>('/canvas-runtime/ai/models', body, { signal, timeout: 20_000 });
    return { items: data.models, truncated: false };
  },
  async list(serviceType: ServiceType, offset: number, limit: number, signal?: AbortSignal) {
    const { data } = await http.get<AiModelConfigListDto>(path, { params: { service_type: serviceType, offset, limit }, signal });
    return { ...data, items: data.items.map(mapAiModelConfig) };
  },
  async get(id: string) { return mapAiModelConfig((await http.get<AiModelConfigDto>(`${path}/${id}`)).data); },
  async create(draft: ConfigDraft) {
    const body: AiModelConfigCreateDto = { ...serializeConfigDraft(draft), service_type: draft.serviceType };
    const result = mapAiModelConfig((await http.post<AiModelConfigDto>(path, body)).data);
    notifyAiConfigsChanged();
    return result;
  },
  async update(id: string, rowVersion: string, draft: ConfigDraft) {
    const body: AiModelConfigUpdateDto = { ...serializeConfigDraft(draft), row_version: rowVersion };
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
