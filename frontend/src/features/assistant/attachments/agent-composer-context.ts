import type { AgentAttachment } from '@host/types/assistant/engine';
import { createContext } from 'react';
/**
 * 当前消息已添加的素材（本地文件、资产库引用等最终都是 AgentAttachment）。
 * 输入区用它把“刚添加的素材”并入 @ 候选，选中只写 @名称，不重复建立附件。
 */
export const ComposerAttachmentsContext = createContext<readonly AgentAttachment[]>([]);
