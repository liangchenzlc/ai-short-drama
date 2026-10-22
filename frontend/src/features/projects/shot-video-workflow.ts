import type { ShotRead } from '../../api/modules/storyboard';
import type { AiModelCapabilitiesDto } from '../../api/types/ai-model-configs';
import type { VideoGenerationRequest } from '../../api/types/generations';

export function videoBlockReason(shot: ShotRead, modelId: string | undefined, capabilities: AiModelCapabilitiesDto | null, loading: boolean): string {
  const duration = shot.video_settings.duration_ms ?? shot.duration_ms;
  if (!shot.image) return '请先在“分镜图”中生成图片或设置主图，再生成视频。';
  if (!modelId) return '请选择已启用的视频模型。';
  if (loading) return '正在核对视频模型能力…';
  if (!capabilities?.known || !capabilities.video_input?.reference_images) return '当前模型尚不支持全能参考图生成视频，请更换模型或刷新能力。';
  if (duration % 1000) return '当前视频接口需要整数秒时长，请调整视频时长。';
  const options = capabilities.video_input;
  if (shot.native_speech) {
    if (!options.audio_references || !options.generate_audio) return '当前模型不支持角色音频参考与原生声音，请更换视频模型；不会退回后期配音。';
    if (!shot.native_speech.reviewed) return '请先编辑并确认分镜对白，无对白镜头也需确认。';
    if (shot.native_speech.voices.some(v => !v.media_id || !v.duration_ms || v.duration_ms < 3000 || v.duration_ms > 7500)) return '说话角色尚未绑定有效样音，请到角色库试听采用 3～7.5 秒音色。';
  }
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
