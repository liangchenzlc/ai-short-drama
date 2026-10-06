import type { AgentArtifact, AgentArtifactKind } from '../../api/types/agent-artifacts';
export const artifactKindLabels: Record<AgentArtifactKind, string> = {
  novel_proposal: '小说候选', text_proposal: '文本候选', script_candidate: '剧本候选', asset_patch: '素材修改', shot_patch: '镜头修改',
  extraction_candidate: '素材提取', storyboard_candidate: '分镜候选', image_candidate: '图片候选', video_candidate: '视频候选',
};
export const artifactStatusLabels = { ready: '待采用', applied: '已采用', rejected: '已拒绝', archived: '已归档' };
export const artifactFieldLabels: Record<string, string> = { name: '名称', label: '称谓', description: '描述', prompt: '图片生成提示词', tags: '标签', scene_time: '场景时间', state: '确认状态', script: '镜头脚本', duration_ms: '镜头时长', video_prompt: '视频生成提示词' };
export const nativeArtifact = (kind: AgentArtifactKind) => ['extraction_candidate', 'storyboard_candidate', 'image_candidate', 'video_candidate'].includes(kind);
export function artifactTarget(artifact: AgentArtifact) {
  const source = artifact.source_snapshot;
  return source.target_kind === 'episode' ? '本集作品' : `${source.target_kind === 'asset' ? '素材' : '镜头'} ${source.target_id ?? ''}`.trim();
}
export function artifactEffect(kind: AgentArtifactKind) {
  return kind === 'novel_proposal' || kind === 'text_proposal' ? '采用会替换本集小说正文，原候选和来源快照保留。'
    : kind === 'script_candidate' ? '采用会设为当前编辑剧本，仍需单独确认定稿。'
    : kind === 'asset_patch' ? '采用会修改这个素材的所列字段，共享引用也可能受到影响。'
    : kind === 'shot_patch' ? '采用会修改这个镜头的所列字段。'
    : kind === 'extraction_candidate' ? '先核对每项素材及同名复用，再加入本集素材库。'
    : kind === 'storyboard_candidate' ? '先核对完整分镜，再选择追加或替换。'
    : '采用会替换指定对象的当前媒体，历史候选保留。';
}
export function artifactVersion(value: string) {
  if (typeof value !== 'string' || !/^[1-9]\d{0,19}$/.test(value) || BigInt(value) > 18446744073709551615n) throw new Error('当前作品版本无法核对，请重新载入后再采用。');
  return value;
}
export function diffValue(field: string, value: unknown) {
  if (field === 'duration_ms' && typeof value === 'number') return `${value / 1000} 秒`;
  if (field === 'state') return value === 'confirmed' ? '已确认' : '未确认';
  if (Array.isArray(value) && value.every(item => typeof item === 'string')) return value.join('、') || '空';
  return typeof value === 'string' ? value || '空' : typeof value === 'number' || typeof value === 'boolean' ? String(value) : '空';
}
