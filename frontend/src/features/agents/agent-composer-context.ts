import { createContext } from 'react';
import type { AgentAttachment } from '../../api/types/agents';

/**
 * 当前消息已添加的素材（本地文件、资产库引用等最终都是 AgentAttachment）。
 * 输入区用它把“刚添加的素材”并入 @ 候选，选中只写 @名称，不重复建立附件。
 */
export const ComposerAttachmentsContext = createContext<readonly AgentAttachment[]>([]);

/** 结构化素材（素材卡片/分镜卡片）与附件共用同一条素材行，只差缩略图来源。 */
export interface ComposerMaterial {
  key: string;
  name: string;
  kind: string;
  thumbnail?: string | null;
  onRemove: () => void;
}
