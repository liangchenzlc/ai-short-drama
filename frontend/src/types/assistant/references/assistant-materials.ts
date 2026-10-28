import type { AssistantSubjectKind, AssistantSubjectOption } from '@host/types/assistant/api';

/**
 * 消息素材：本地上传、资产库、素材卡片、分镜卡片在用户看来是同一件事。
 * 这里只承载「结构化对象」那一类（素材卡片/分镜卡片/节点），文件类走 attachment_ids。
 * 素材只属于添加它的那条消息；草稿只保留最小身份，恢复时重新核对，发送成功后清空。
 */
export interface MessageMaterial extends AssistantSubjectOption {}

export interface MessageMaterialDraft {
  kind: AssistantSubjectKind;
  id: string;
  name: string;
  revision?: string;
}
