import { http } from '@host/api/http';
import type { RunningHubWorkflowDto, ServiceTypeDto } from '@host/types/ai-config/api';
import type { ModelTestDto, ModelTestRequestDto, ProtocolProviderDto } from '@host/types/ai-config/tools';
const runtime = '/canvas-runtime';
function checkedTestReceipt(value: ModelTestDto, expectedId?: string): ModelTestDto {
  if (
    !value ||
    typeof value.id !== 'string' ||
    !/^[A-Za-z0-9_-]{1,128}$/.test(value.id) ||
    !['queued', 'running', 'succeeded', 'failed', 'cancelled'].includes(value.status) ||
    typeof value.canCancel !== 'boolean' ||
    (expectedId && value.id !== expectedId)
  ) {
    throw new Error('模型测试回执无效，请核对原测试。');
  }
  return value;
}
export const aiConfigTools = {
  async runningHubSchema(id: string, rowVersion: string, signal?: AbortSignal) {
    return (
      await http.post<RunningHubWorkflowDto>(
        `${runtime}/ai/models/${encodeURIComponent(id)}/runninghub-schema`,
        { row_version: rowVersion },
        { signal },
      )
    ).data;
  },
  async protocols(kind: ServiceTypeDto, signal?: AbortSignal) {
    return (
      await http.get<{ providers: ProtocolProviderDto[] }>(`${runtime}/plugins/catalog`, {
        params: { scope: 'user.custom-channel', capability: kind },
        signal,
      })
    ).data.providers;
  },
  async startTest(body: ModelTestRequestDto, operationId: string) {
    return checkedTestReceipt(
      (
        await http.post<ModelTestDto>(`${runtime}/model-tests`, body, {
          headers: { 'Idempotency-Key': operationId },
        })
      ).data,
    );
  },
  async readTest(id: string, signal?: AbortSignal) {
    return checkedTestReceipt(
      (await http.get<ModelTestDto>(`${runtime}/model-tests/${encodeURIComponent(id)}`, { signal })).data,
      id,
    );
  },
  async cancelTest(id: string) {
    return checkedTestReceipt(
      (await http.post<ModelTestDto>(`${runtime}/model-tests/${encodeURIComponent(id)}/cancel`)).data,
      id,
    );
  },
};
