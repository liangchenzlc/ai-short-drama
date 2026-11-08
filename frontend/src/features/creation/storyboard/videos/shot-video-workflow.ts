import type { AiModelCapabilitiesDto } from '@host/types/ai-config/api';
import type { ShotRead } from '@host/types/creation/storyboard/shots';
import type { VideoGenerationRequest } from '@host/types/generations/api';
export function videoBlockReason(
  shot: ShotRead,
  modelId: string | undefined,
  capabilities: AiModelCapabilitiesDto | null,
  loading: boolean,
  referenceCount: number,
): string {
  const duration = shot.video_settings.duration_ms ?? shot.duration_ms;
  if (!modelId) return '请选择已启用的视频模型。';
  if (loading) return '正在核对视频模型能力…';
  if (!capabilities?.known) return '视频模型能力尚未确认，请刷新能力或更换模型。';
  if (referenceCount > 9) return '视频参考图片最多 9 张，请移除多余图片。';
  if (referenceCount && !capabilities.video_input?.reference_images)
    return '当前模型不支持参考图片，请移除图片或更换模型。';
  if (duration % 1000) return '当前视频接口需要整数秒时长，请调整视频时长。';
  const options = capabilities.video_input;
  if (shot.native_speech) {
    if (!options?.audio_references || !options.generate_audio)
      return '当前模型不支持角色音频参考与原生声音，请更换视频模型；不会退回后期配音。';
    if (!shot.native_speech.reviewed) return '请先编辑并确认分镜对白，无对白镜头也需确认。';
    if (
      shot.native_speech.voices.some(
        (v) => !v.media_id || !v.duration_ms || v.duration_ms < 3000 || v.duration_ms > 7500,
      )
    )
      return '说话角色尚未绑定有效样音，请到角色库试听采用 3～7.5 秒音色。';
  }
  if (options?.duration_seconds && !options.duration_seconds.includes(duration / 1000))
    return `当前模型支持 ${options.duration_seconds.join('、')} 秒，请调整视频时长或更换模型。`;
  if (options?.resolutions && !options.resolutions.includes(shot.video_settings.resolution))
    return `当前模型支持 ${options.resolutions.join('、')}，请调整视频清晰度。`;
  return '';
}
export function shotVideoRequest(shot: ShotRead, modelId: string): VideoGenerationRequest {
  return {
    config_id: modelId,
    source: {
      scene: 'shot_video',
      shot_id: shot.id,
      row_version: shot.row_version,
      context_hash: shot.video_context_hash,
    },
    parameters: {
      resolution: shot.video_settings.resolution,
      duration_ms: shot.video_settings.duration_ms ?? shot.duration_ms,
    },
  };
}
