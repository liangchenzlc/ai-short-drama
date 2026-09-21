export interface WorkflowShot {
  id: string;
  row_version: string;
  context_hash: string;
  image_settings: { layout: 'single' | 'four' | 'five' | 'nine'; aspect: 'inherit' | '16:9' | '9:16' | '1:1' | '4:3' | '3:4'; resolution: '1K' | '2K' | '4K' };
  image?: { media_id: string } | null;
}

export function novelScriptRequest(projectId: string, episodeId: string, contentVersion: string, instructions: string) {
  return { source: { scene: 'novel_script' as const, project_id: projectId, episode_id: episodeId, content_version: contentVersion }, instructions, parameters: { max_output_tokens: 8192 } };
}

export function scriptShotsRequest(projectId: string, episodeId: string, scriptId: string, contentVersion: string, instructions: string) {
  return { source: { scene: 'script_shots' as const, project_id: projectId, episode_id: episodeId, script_id: scriptId, content_version: contentVersion }, instructions, parameters: { max_output_tokens: 8192 } };
}

export function shotImageRequest(shot: WorkflowShot, prompt: string, referenceMediaIds: readonly string[], count: number, episodeAspect: '16:9' | '9:16' = '16:9') {
  const ids = [...new Set(referenceMediaIds)];
  return {
    source: { scene: 'shot_image' as const, shot_id: shot.id, layout: shot.image_settings.layout, context_mode: 'saved' as const, row_version: shot.row_version, context_hash: shot.context_hash },
    input: { prompt, reference_media_ids: ids },
    parameters: { aspect: shot.image_settings.aspect === 'inherit' ? episodeAspect : shot.image_settings.aspect, resolution: shot.image_settings.resolution, count },
  };
}

export function shotImageApplyRequest(shot: WorkflowShot, acknowledgeStaleSource: boolean, episodeAspect: '16:9' | '9:16' = '16:9') {
  return {
    target: { type: 'shot_image' as const, id: shot.id },
    expected_media_id: shot.image?.media_id ?? null,
    expected_row_version: shot.row_version,
    expected_context_hash: shot.context_hash,
    acknowledge_stale_source: acknowledgeStaleSource,
    parameters: { ...shot.image_settings, aspect: shot.image_settings.aspect === 'inherit' ? episodeAspect : shot.image_settings.aspect },
  };
}

export function moveShot<T extends { id: string }>(shots: readonly T[], id: string, direction: -1 | 1): T[] {
  const index = shots.findIndex((shot) => shot.id === id);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= shots.length) return shots as T[];
  const next = [...shots];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function assetPatch<T extends { row_version: string; name?: string; tags?: string[] }>(draft: T, confirmShared: boolean) {
  return {
    ...draft,
    ...(draft.name === undefined ? {} : { name: draft.name.trim() }),
    ...(draft.tags === undefined ? {} : { tags: [...new Set(draft.tags.map((tag) => tag.trim()).filter(Boolean))] }),
    confirm_shared: confirmShared,
  };
}

export function assetConfirmRequest(asset: { row_version: string; media_id?: string | null }, mediaId: string, confirmShared: boolean) {
  return { row_version: asset.row_version, media_id: mediaId, expected_media_id: asset.media_id ?? null, confirm_shared: confirmShared };
}
