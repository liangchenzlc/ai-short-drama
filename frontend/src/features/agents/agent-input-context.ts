import type { AgentAttachment, AgentModel } from '../../api/types/agents';

export function attachmentInputIssue(attachments: AgentAttachment[], model?: AgentModel, videoAudio: 'include' | 'visual_only' = 'include'): string {
  if (attachments.length > 16) return '一次消息最多添加 16 项附件。';
  const capabilities = model?.input_capabilities;
  for (const attachment of attachments) {
    if (attachment.kind === 'image' && !capabilities?.image) return '当前模型不支持图片理解，请选择支持视觉输入的模型。';
    if (attachment.kind === 'audio' && !capabilities?.audio) return '当前模型不支持音频理解，请选择支持音频输入的模型。';
    if (attachment.kind === 'video') {
      if (capabilities?.video !== 'sampled_frames') return '当前模型不支持视频画面理解，请选择支持视觉输入的模型。';
      if (attachment.metadata.has_audio && videoAudio === 'include' && !capabilities.audio) return '视频含有声音，当前模型不支持音频理解。请换模型，或明确选择只理解画面。';
    }
  }
  return '';
}

export const attachmentAccept = {
  image: '.png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp',
  text: '.txt,.md,text/plain,text/markdown',
  video: '.mp4,.webm,video/mp4,video/webm',
  audio: '.mp3,.wav,.m4a,audio/mpeg,audio/wav,audio/mp4',
};
const limits = { text: 1, image: 20, audio: 20, video: 50 };
export function attachmentFileIssue(file: Pick<File, 'size' | 'name'>, kind: AgentAttachment['kind']): string {
  if (!file.size) return '文件为空，请选择有效文件。';
  if (file.size > limits[kind] * 1024 * 1024) return `${file.name} 超过${limits[kind]} MiB上传限制。`;
  return '';
}
