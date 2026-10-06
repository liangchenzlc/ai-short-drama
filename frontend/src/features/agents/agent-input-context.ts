import type { AgentAttachment } from '../../api/types/agents';

export function attachmentInputIssue(attachments: AgentAttachment[]): string {
  if (attachments.length > 16) return '一次消息最多添加 16 项附件。';
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
