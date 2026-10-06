import type { ExtractionCandidate, ExtractionResult, ExtractionApplyRequest } from '../../api/modules/asset-extraction';
import type { AssetDraft, AssetKind } from '../../api/modules/assets';
import type { GenerationSummary } from '../../api/types/generations';

export class ExtractionReviewError extends Error {}

export function extractionDraftError(draft: Pick<AssetDraft, 'name' | 'description' | 'prompt'>): string {
  const missing = [!draft.name.trim() && '名称', !draft.description.trim() && '描述', !draft.prompt.trim() && '图片生成提示词'].filter(Boolean);
  return missing.length ? `请补充${missing.join('、')}，或明确放弃此项修改。` : '';
}

export function missingExtractionTaskIds(previous: readonly GenerationSummary[], incoming: readonly GenerationSummary[]): string[] {
  const visible = new Set(incoming.map(task => task.generation_id));
  return previous.filter(task => (task.status === 'queued' || task.status === 'running') && !visible.has(task.generation_id)).map(task => task.generation_id);
}

export function scriptAssetsRequest(projectId: string, episodeId: string, scriptId: string, contentVersion: string, kinds: AssetKind[], instructions: string) {
  if (!kinds.length) throw new ExtractionReviewError('请至少选择一种提取范围。');
  return {
    source: { scene: 'script_assets' as const, project_id: projectId, episode_id: episodeId, script_id: scriptId, content_version: contentVersion },
    extraction: { kinds: [...new Set(kinds)] }, instructions, parameters: {},
  };
}

export function defaultAdoption(item: ExtractionCandidate): string {
  if (item.duplicate_candidates?.length) return '';
  if (!item.matches.length) return 'create';
  if (item.matches.length === 1 && item.matches[0].exact) return item.matches[0].asset_id;
  return '';
}

export function extractionApplyRequest(result: ExtractionResult, selected: ReadonlySet<string>, choices: Record<string, string>, contentVersion: string): ExtractionApplyRequest {
  if (result.stale) throw new ExtractionReviewError('剧本已变化，请基于当前已确认剧本重新提取。');
  const items = result.items.filter(item => selected.has(item.candidate_id) && !item.applied).map(item => {
    const choice = choices[item.candidate_id];
    if (choice === 'create') return { candidate_id: item.candidate_id, action: 'create' as const, confirm_duplicate: item.matches.length > 0 || !!item.duplicate_candidates?.length };
    const match = item.matches.find(match => match.asset_id === choice);
    if (!match) throw new ExtractionReviewError(`请先核对「${item.draft.name}」的采用方式。`);
    return { candidate_id: item.candidate_id, action: 'reuse' as const, asset_id: match.asset_id, expected_row_version: match.row_version };
  });
  if (!items.length) throw new ExtractionReviewError('请至少选择一项未采用的素材。');
  return { result_version: result.result_version, content_version: contentVersion, items };
}
