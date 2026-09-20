import { ApiError, errorMessage } from '../../api/http';
import type { GenerationSummary, TaskStatus } from '../../api/types/generations';
export const kindLabels = { text: '文本', image: '图片', video: '视频' };
export const statusLabels: Record<TaskStatus, string> = { queued: '排队中', running: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消' };
export function taskLabel(task: GenerationSummary) {
  if (task.status !== 'running') return statusLabels[task.status];
  return task.next_action === 'save' ? '保存结果中' : task.next_action === 'poll' ? '等待模型结果' : '生成中';
}
export function dateLabel(value?: string | null) {
  if (!value) return '—';
  const date = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false });
}
export function generationError(error: unknown): string {
  if (error instanceof ApiError) {
    const messages: Record<string, string> = {
      generation_default_config_missing: '尚未设置此类型的默认模型。请在“模型配置”中选择一个已启用的模型，或前往 AI 配置设置默认模型。',
      generation_unsupported_image_size: '当前图片适配器无法转换这组画面比例或分辨率。请先清空这两项，使用模型默认尺寸；也可填写服务商支持的明确像素尺寸（如 1024x1024），画面比例留空。',
      generation_unsupported_protocol: '当前服务地址的生成协议尚未适配，请检查地址或使用已支持的模型服务。',
      generation_unsupported_parameters: '当前适配器不支持这组参数或参考图。请先移除可选参数和参考图重试，再按该模型的能力填写。',
    };
    if (Object.hasOwn(messages, error.code)) return messages[error.code];
    if (error.status === 422 && error.fields.length) return [...new Set(error.fields.map((field) => field.message))].join(' ');
    if (error.status === 409) return '内容或状态已变化，或幂等键与请求不一致。请刷新后核对；创建结果不明时，可保持表单原样重试查询原任务。';
    if (error.status === 404) return '服务端记录不存在，请检查 ID 或刷新列表。';
    if (error.status === 400) return '当前配置、参数或任务状态不支持此操作，请检查模型配置与参数后重试。';
  }
  return errorMessage(error);
}
