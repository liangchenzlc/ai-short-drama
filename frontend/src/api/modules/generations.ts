import { http } from '../http';
import type { GenerationDetail, GenerationFilters, GenerationReceipt, GenerationRecord, GenerationSummary, ImageGenerationRequest, Page, TextGenerationRequest, VideoGenerationRequest } from '../types/generations';
const root = '/ai/generations';
const headers = (key: string) => ({ 'Idempotency-Key': key });
export const generations = {
  async generateText(body: TextGenerationRequest, key: string) { return (await http.post<GenerationReceipt>(`${root}/text`, body, { headers: headers(key) })).data; },
  async generateImage(body: ImageGenerationRequest, key: string) { return (await http.post<GenerationReceipt>(`${root}/image`, body, { headers: headers(key) })).data; },
  async generateVideo(body: VideoGenerationRequest, key: string) { return (await http.post<GenerationReceipt>(`${root}/video`, body, { headers: headers(key) })).data; },
  async list(params: GenerationFilters, signal?: AbortSignal) { return (await http.get<Page<GenerationSummary>>(root, { params, signal })).data; },
  async detail(id: string, signal?: AbortSignal) { return (await http.get<GenerationDetail>(`${root}/${id}`, { signal })).data; },
  async records(id: string, signal?: AbortSignal) { return (await http.get<{ items: GenerationRecord[] }>(`${root}/${id}/records`, { signal })).data; },
  async cancel(id: string) { return (await http.post<GenerationDetail>(`${root}/${id}/cancel`)).data; },
  async retry(id: string, key: string, configId?: string) { return (await http.post<GenerationReceipt>(`${root}/${id}/retry`, configId ? { config_id: configId } : {}, { headers: headers(key) })).data; },
  async resume(id: string) { return (await http.post<GenerationDetail>(`${root}/${id}/resume`)).data; },
};
