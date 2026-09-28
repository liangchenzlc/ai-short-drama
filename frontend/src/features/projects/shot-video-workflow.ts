import type { ShotRead } from '../../api/modules/storyboard';
import type { AiModelCapabilitiesDto } from '../../api/types/ai-model-configs';
import type { ApplyAssetRequest, VideoGenerationRequest } from '../../api/types/generations';

export function videoBlockReason(shot: ShotRead, modelId: string | undefined, capabilities: AiModelCapabilitiesDto | null, loading: boolean): string {
  const duration = shot.video_settings.duration_ms ?? shot.duration_ms;
  if (!shot.image) return '请先在“分镜图”中采用一张图片，再生成视频。';
  if (shot.image.is_stale) return '分镜内容已变化，请先核对并采用当前内容的分镜图。';
  if (!shot.script.trim()) return '请先填写本镜脚本。';
  if (!modelId) return '请选择已启用的视频模型。';
  if (loading) return '正在核对视频模型能力…';
  if (!capabilities?.known || !capabilities.video_input?.reference_images) return '当前模型尚不支持全能参考图生成视频，请更换模型或刷新能力。';
  if (duration % 1000) return '当前视频接口需要整数秒时长，请调整视频时长。';
  const options = capabilities.video_input;
  if (options.duration_seconds && !options.duration_seconds.includes(duration / 1000)) return `当前模型支持 ${options.duration_seconds.join('、')} 秒，请调整视频时长或更换模型。`;
  if (options.resolutions && !options.resolutions.includes(shot.video_settings.resolution)) return `当前模型支持 ${options.resolutions.join('、')}，请调整视频清晰度。`;
  return '';
}

export function shotVideoRequest(shot: ShotRead, modelId: string): VideoGenerationRequest {
  if (!shot.image) throw new Error('缺少分镜参考图');
  return { config_id: modelId, source: { scene: 'shot_video', shot_id: shot.id, row_version: shot.row_version,
    context_hash: shot.video_context_hash, reference_media_id: shot.image.media_id },
    parameters: { resolution: shot.video_settings.resolution, duration_ms: shot.video_settings.duration_ms ?? shot.duration_ms } };
}

export function shotVideoApplyRequest(shot: ShotRead): ApplyAssetRequest {
  return { target: { type: 'shot_video', id: shot.id }, expected_media_id: shot.video?.media_id ?? null,
    expected_row_version: shot.row_version, expected_context_hash: shot.video_context_hash };
}
