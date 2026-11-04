import type { ServiceType } from '@host/types/ai-config/editor/config-model';
export const serviceLabels: Record<ServiceType, string> = {
  audio: '音频模型',
  text: '文本模型',
  image: '图片模型',
  video: '视频模型',
};
