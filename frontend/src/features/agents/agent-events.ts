import type { AgentEvent, AgentReviewStep } from '../../api/types/agents';

/** Preserve partial frames across arbitrary UTF-8 network chunks. */
export function takeSseFrames(buffer: string): { frames: string[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n');
  const parts = normalized.split('\n\n');
  return { frames: parts.slice(0, -1), rest: parts.at(-1) ?? '' };
}
export function parseAgentEvent(frame: string): AgentEvent | null {
  const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!data) return null; // Includes heartbeat comments.
  try {
    const event = JSON.parse(data) as AgentEvent;
    return Number.isSafeInteger(event.seq) && event.seq > 0 && typeof event.event_type === 'string'
      && event.payload && typeof event.payload === 'object' ? event : null;
  } catch { return null; }
}
export interface DeltaChunk { turn: string; offset: number; text: string }
export function addDelta(chunks: DeltaChunk[], event: AgentEvent): DeltaChunk[] {
  const { turn_id, offset, delta } = event.payload;
  if (typeof turn_id !== 'string' || !Number.isSafeInteger(offset) || (offset as number) < 0 || typeof delta !== 'string') return chunks;
  if (chunks.some(chunk => chunk.turn === turn_id && chunk.offset === offset)) return chunks;
  return [...chunks, { turn: turn_id, offset: offset as number, text: delta }];
}
export function latestDelta(chunks: DeltaChunk[]): string {
  const turn = chunks.at(-1)?.turn;
  return chunks.filter(chunk => chunk.turn === turn).sort((a, b) => a.offset - b.offset).map(chunk => chunk.text).join('');
}

export const agentTaskLabels: Record<string, string> = {
  novel: '小说', script: '剧本', extract: '素材提取', storyboard: '分镜', asset_patch: '素材修改',
  shot_patch: '镜头修改', image: '图片', video: '视频',
};
export const modelCanCollaborate = (model: { verified: boolean; tool_calling: boolean; tool_result_continuation: boolean } | undefined) =>
  !!model?.verified && model.tool_calling && model.tool_result_continuation;

export function reviewTargetLabel(step: AgentReviewStep) {
  if (typeof step.target_label === 'string' && step.target_label.trim()) return step.target_label.trim();
  if (!step.target_id) return '本集作品';
  const kind = step.target_kind === 'asset' || step.kind === 'asset_patch' ? '素材'
    : step.target_kind === 'shot' || step.kind === 'shot_patch' ? '镜头' : '作品';
  return `选定${kind}`;
}
/** Only frozen, user-facing settings are shown; never stringify tool/source payloads. */
export function reviewParameterLabels(parameters: Record<string, unknown>) {
  const labels: string[] = [];
  if (typeof parameters.resolution === 'string' && /^(480p|720p|1080p|[124]K)$/i.test(parameters.resolution)) labels.push(`清晰度 ${parameters.resolution}`);
  if (typeof parameters.aspect === 'string' && /^\d{1,2}:\d{1,2}$/.test(parameters.aspect)) labels.push(`画幅 ${parameters.aspect}`);
  if (parameters.aspect === 'inherit') labels.push('画幅沿用本集');
  if (typeof parameters.duration_ms === 'number' && Number.isFinite(parameters.duration_ms) && parameters.duration_ms > 0) labels.push(`时长 ${parameters.duration_ms / 1000} 秒`);
  const layouts: Record<string, string> = { single: '单图', four: '四宫格', five: '五宫格', nine: '九宫格', grid4: '四宫格', grid5: '五宫格', grid9: '九宫格', '2x2': '四宫格', '3x3': '九宫格' };
  if (typeof parameters.layout === 'string' && layouts[parameters.layout]) labels.push(`布局 ${layouts[parameters.layout]}`);
  if (Array.isArray(parameters.reference_media_ids)) labels.push(`参考图片 ${parameters.reference_media_ids.length} 张`);
  return labels;
}
