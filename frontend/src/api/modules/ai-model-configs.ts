import { http } from '../http';
import type { AiModelConfigDto, AiModelConfigCreateDto, AiModelConfigUpdateDto, AiModelConfigListDto, AiModelCapabilitiesDto, ModelDiscoveryDto, ModelDiscoveryRequestDto, RuntimeModelDiscoveryRequestDto, ServiceTypeDto } from '../types/ai-model-configs';

const path = '/ai-model-configs';
export const aiModelConfigs = {
  async capabilities(id: string, signal?: AbortSignal) {
    return (await http.get<AiModelCapabilitiesDto>(`${path}/${encodeURIComponent(id)}/capabilities`, { signal })).data;
  },
  async discoverModels(body: ModelDiscoveryRequestDto, signal?: AbortSignal) {
    return (await http.post<ModelDiscoveryDto>(`${path}/discover-models`, body, { signal, timeout: 20_000 })).data;
  },
  async discoverRuntimeModels(body: RuntimeModelDiscoveryRequestDto, signal?: AbortSignal) {
    return (await http.post<{ models: { id: string }[] }>('/canvas-runtime/ai/models', body, { signal, timeout: 20_000 })).data;
  },
  async list(serviceType: ServiceTypeDto, offset: number, limit: number, signal?: AbortSignal) {
    return (await http.get<AiModelConfigListDto>(path, { params: { service_type: serviceType, offset, limit }, signal })).data;
  },
  async get(id: string) { return (await http.get<AiModelConfigDto>(`${path}/${id}`)).data; },
  async create(body: AiModelConfigCreateDto) {
    return (await http.post<AiModelConfigDto>(path, body)).data;
  },
  async update(id: string, body: AiModelConfigUpdateDto) {
    return (await http.patch<AiModelConfigDto>(`${path}/${id}`, body)).data;
  },
  async remove(id: string, rowVersion: string) {
    await http.delete(`${path}/${id}`, { params: { row_version: rowVersion } });
  },
  async setDefault(id: string, rowVersion: string) {
    return (await http.put<AiModelConfigDto>(`${path}/${id}/default`, { row_version: rowVersion })).data;
  },
};
