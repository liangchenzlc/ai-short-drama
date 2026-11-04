import { http } from '@host/api/http';
import type {
  AiModelCapabilitiesDto,
  AiModelConfigCreateDto,
  AiModelConfigDto,
  AiModelConfigListDto,
  AiModelConfigUpdateDto,
  ModelDiscoveryDto,
  ModelDiscoveryRequestDto,
  RuntimeModelDiscoveryRequestDto,
  ServiceTypeDto,
  ModelConfigurationOptionsDto,
} from '@host/types/ai-config/api';
const path = '/ai-model-configs';
export const aiModelConfigs = {
  async options(serviceType: ServiceTypeDto, signal?: AbortSignal) {
    return (await http.get<ModelConfigurationOptionsDto>(`${path}/options`, { params: { service_type: serviceType }, signal })).data;
  },
  async capabilities(id: string, signal?: AbortSignal) {
    return (await http.get<AiModelCapabilitiesDto>(`${path}/${encodeURIComponent(id)}/capabilities`, { signal })).data;
  },
  async discoverModels(body: ModelDiscoveryRequestDto, signal?: AbortSignal) {
    return (
      await http.post<ModelDiscoveryDto>(`${path}/discover-models`, body, {
        signal,
        timeout: 20_000,
      })
    ).data;
  },
  async discoverRuntimeModels(body: RuntimeModelDiscoveryRequestDto, signal?: AbortSignal) {
    return (
      await http.post<{ models: { id: string }[] }>('/canvas-runtime/ai/models', body, { signal, timeout: 20_000 })
    ).data;
  },
  async list(serviceType: ServiceTypeDto, offset: number, limit: number, signal?: AbortSignal, createKey?: string) {
    return (
      await http.get<AiModelConfigListDto>(path, {
        params: { service_type: serviceType, offset, limit, ...(createKey ? { create_key: createKey } : {}) },
        signal,
      })
    ).data;
  },
  async get(id: string) {
    return (await http.get<AiModelConfigDto>(`${path}/${id}`)).data;
  },
  async create(body: AiModelConfigCreateDto, key: string) {
    return (await http.post<AiModelConfigDto>(path, body, { headers: { 'Idempotency-Key': key } })).data;
  },
  async update(id: string, body: AiModelConfigUpdateDto) {
    return (await http.patch<AiModelConfigDto>(`${path}/${id}`, body)).data;
  },
  async remove(id: string, rowVersion: string) {
    await http.delete(`${path}/${id}`, { params: { row_version: rowVersion } });
  },
  async setDefault(id: string, rowVersion: string) {
    return (
      await http.put<AiModelConfigDto>(`${path}/${id}/default`, {
        row_version: rowVersion,
      })
    ).data;
  },
};
