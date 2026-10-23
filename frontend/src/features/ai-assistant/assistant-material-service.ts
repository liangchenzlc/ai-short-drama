import { assistantApi } from '../../api/modules/assistant';
import type { AssistantMessageContext, AssistantSubjectSelection } from '../../api/types/assistant';
import { materialReference, retainedSubjectKeys, type MessageMaterial, type MessageMaterialDraft } from './assistant-materials';

/** 添加素材时读取一次元数据，供药丸显示名称；版本以读到的为准。 */
export async function inspectMaterials(
  projectId: string,
  context: AssistantMessageContext,
  references: readonly MessageMaterialDraft[],
): Promise<MessageMaterial[]> {
  if (!context.selected?.length) return [];
  const inspected = await assistantApi.inspectSubjects(context.selected.map(item => ({
    project_id: projectId,
    source_kind: context.kind,
    source_id: context.id,
    kind: item.kind,
    id: item.id,
    revision: item.revision ?? context.revision,
  })));
  return inspected.map(item => ({
    ...item,
    name: references.find(reference => reference.kind === item.kind && reference.id === item.id)?.name ?? item.id,
    project_name: '当前作品',
    source_name: context.id,
  }));
}

/**
 * 组装本条消息的引用集合：保留任务里已有快照（历史素材模型仍认得），
 * 再并入本条消息新增的素材。水位发送前现读，避免与其它输入互相覆盖。
 */
export async function subjectSelectionFor(taskId: string | null, materials: readonly MessageMaterial[]): Promise<AssistantSubjectSelection | null> {
  if (!materials.length) return null;
  const metadata = taskId ? await assistantApi.subjects(taskId) : null;
  return {
    expected_input_revision: metadata?.input_revision ?? '0',
    retain_keys: retainedSubjectKeys(metadata?.items ?? [], materials),
    subjects: materials.map(materialReference),
  };
}
