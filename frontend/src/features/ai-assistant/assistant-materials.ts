import type {
  AssistantSubjectKind,
  AssistantSubjectOption,
  AssistantSubjectReference,
} from '../../api/types/assistant';

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

export function materialIdentity(item: { project_id: string; source_kind: string; source_id: string; kind: string; id: string }) {
  return JSON.stringify([item.project_id, item.source_kind, item.source_id, item.kind, item.id]);
}

/** 同一对象只保留一份，整条消息合计最多 16 项（与附件共用额度）。 */
export function mergeMaterials(existing: readonly MessageMaterial[], incoming: readonly MessageMaterial[]): MessageMaterial[] {
  return [...existing.filter(item => !incoming.some(next => materialIdentity(item) === materialIdentity(next))), ...incoming]
    .slice(0, 16);
}

export function materialReference(item: MessageMaterial): AssistantSubjectReference {
  return {
    project_id: item.project_id,
    source_kind: item.source_kind,
    source_id: item.source_id,
    kind: item.kind,
    id: item.id,
    revision: item.revision,
    // 服务端只允许画布节点带独立内容版本；校验和只在格式合法时回传。
    ...(item.kind === 'node' && item.content_revision ? { content_revision: item.content_revision } : {}),
    ...(item.checksum && /^[0-9a-f]{64}$/.test(item.checksum) ? { checksum: item.checksum } : {}),
  };
}

/** 本条消息重新提交的素材会覆盖任务里的旧快照，不能再写进 retain_keys，否则服务端判为重复引用。 */
export function retainedSubjectKeys(
  existing: readonly { subject_key: string; project_id: string; source_kind: string; source_id: string; kind: string; id: string }[],
  materials: readonly MessageMaterial[],
): string[] {
  const incoming = new Set(materials.map(materialIdentity));
  return existing.filter(item => !incoming.has(materialIdentity(item))).map(item => item.subject_key);
}
