import { useCallback, useEffect, useImperativeHandle, useMemo, useState, type Ref } from 'react';
import { Alert, Button, InputNumber, Select } from 'antd';
import { assetLibraries, type AssetRead, type LibraryAssetRead } from '../../api/modules/assets';
import { storyboardApi, type ShotRead } from '../../api/modules/storyboard';
import { projectsApi } from '../../api/modules/projects';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import { generationReferences } from '../../api/modules/generation-references';
import type { AiModelCapabilitiesDto } from '../../api/types/ai-model-configs';
import type { AgentConversation, AgentTaskSpec } from '../../api/types/agents';
import { errorMessage } from '../../api/http';
import { EpisodeModelSelect } from '../projects/EpisodeModelSelect';
import { imageAspects, imageLayouts, imageResolutions } from '../projects/shot-generation-settings';
import { imageGenerationBlockReason } from '../projects/shot-image-workflow';
import { videoBlockReason } from '../projects/shot-video-workflow';
import { reviewParameterLabels } from './agent-events';

export interface AgentMediaTaskController { prepare: (instructions: string) => Promise<{ task: AgentTaskSpec; confirmation: string }> }
export class AgentMediaTaskError extends Error {}
const assetKinds: Record<string, string> = { character: '角色', scene: '场景', prop: '道具' };
const shotLabel = (shot: ShotRead) => `第 ${shot.position} 镜头 · ${shot.script.replace(/\s+/g, ' ').slice(0, 64) || '未填写脚本'}`;
const assetLabel = (asset: AssetRead) => `${assetKinds[asset.kind]}：${asset.name}`;

export function AgentMediaTaskComposer({ conversation, kind, disabled, onBlockReason, ref }: {
  conversation: AgentConversation; kind: 'image' | 'video'; disabled: boolean;
  onBlockReason: (reason: string) => void; ref: Ref<AgentMediaTaskController>;
}) {
  const { project_id: projectId, episode_id: episodeId } = conversation;
  const shotsApi = useMemo(() => storyboardApi(projectId, episodeId), [projectId, episodeId]);
  const [assets, setAssets] = useState<LibraryAssetRead[]>([]);
  const [shots, setShots] = useState<ShotRead[]>([]);
  const [episodeAspect, setEpisodeAspect] = useState('16:9');
  const [targetKind, setTargetKind] = useState<'asset' | 'shot'>(kind === 'video' ? 'shot' : 'asset');
  const [targetId, setTargetId] = useState<string>();
  const [selection, setSelection] = useState('');
  const [modelId, setModelId] = useState<string>();
  const onResolvedModel = useCallback((id: string | undefined) => setModelId(id), []);
  const [capabilities, setCapabilities] = useState<AiModelCapabilitiesDto | null>(null);
  const [capLoading, setCapLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [capError, setCapError] = useState('');
  const [revision, setRevision] = useState(0);
  const [references, setReferences] = useState<string[]>([]);
  const [referenceLoading, setReferenceLoading] = useState(false);
  const [referenceError, setReferenceError] = useState('');
  const [resolution, setResolution] = useState<string>();
  const [aspect, setAspect] = useState<string>();
  const [layout, setLayout] = useState<string>();
  const [duration, setDuration] = useState<number>();
  const [count, setCount] = useState(1);
  const targetShot = targetKind === 'shot' ? shots.find(item => item.id === targetId) : undefined;
  const targetAsset = targetKind === 'asset' ? assets.find(item => item.id === targetId) : undefined;

  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    async function allAssets() {
      const items: LibraryAssetRead[] = []; let offset = 0;
      do { const page = await assetLibraries.list({ kind: 'episode', projectId, episodeId }, { offset, limit: 100 }, controller.signal);
        if (controller.signal.aborted) return items; items.push(...page.items); offset += page.items.length; if (!page.items.length || offset >= page.total) return items;
      } while (!controller.signal.aborted); return items;
    }
    async function allShots() {
      const items: ShotRead[] = []; let offset = 0;
      do { const page = await shotsApi.shots(controller.signal, false, offset, 100);
        if (controller.signal.aborted) return items; items.push(...page.items); offset += page.items.length; if (!page.items.length || offset >= page.total) return items;
      } while (!controller.signal.aborted); return items;
    }
    Promise.all([allAssets(), allShots(), projectsApi.getEpisode(projectId, episodeId, controller.signal)])
      .then(([nextAssets, nextShots, episode]) => { if (!controller.signal.aborted) { setAssets(nextAssets); setShots(nextShots); setEpisodeAspect(episode.aspect); } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, episodeId, shotsApi, revision]);
  useEffect(() => {
    const controller = new AbortController(); setCapabilities(null); setCapError(''); setCapLoading(!!modelId);
    if (modelId) void aiModelConfigs.capabilities(modelId, controller.signal)
      .then(next => { if (!controller.signal.aborted) setCapabilities(next); })
      .catch(cause => { if (!controller.signal.aborted) setCapError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setCapLoading(false); });
    return () => controller.abort();
  }, [modelId, revision]);
  useEffect(() => {
    const controller = new AbortController(); setReferences([]); setReferenceError(''); setReferenceLoading(!!targetId && kind === 'image');
    if (targetId && kind === 'image') void generationReferences(targetKind, targetId).list(controller.signal)
      .then(page => { if (!controller.signal.aborted) setReferences(page.items.map(item => item.media_id)); })
      .catch(cause => { if (!controller.signal.aborted) setReferenceError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setReferenceLoading(false); });
    return () => controller.abort();
  }, [targetId, targetKind, kind, revision]);

  const inheritedImages = targetShot?.asset_ids.flatMap(id => { const asset = assets.find(item => item.id === id); return asset?.state === 'confirmed' && asset.media_id ? [asset.media_id] : []; }) ?? [];
  const referenceCount = kind === 'video' ? targetShot?.image ? 1 : 0 : new Set([...inheritedImages, ...references]).size;
  function settings(shot = targetShot, currentAspect = episodeAspect): { resolution: string; aspect: string; layout?: string; duration_ms?: number } {
    const imageAspect = shot?.image_settings.aspect;
    return kind === 'image' ? { resolution: resolution ?? shot?.image_settings.resolution ?? '2K', aspect: aspect ?? (imageAspect && imageAspect !== 'inherit' ? imageAspect : currentAspect), ...(targetKind === 'shot' ? { layout: layout ?? shot?.image_settings.layout ?? 'single' } : {}) }
      : { resolution: resolution ?? shot?.video_settings?.resolution ?? '720p', aspect: aspect ?? currentAspect, duration_ms: duration !== undefined ? duration * 1000 : shot?.video_settings?.duration_ms ?? shot?.duration_ms ?? 3000 };
  }
  const parameters = settings();
  const defaultResolution = kind === 'image' ? targetShot?.image_settings.resolution ?? '2K' : targetShot?.video_settings?.resolution ?? '720p';
  const defaultAspect = kind === 'image' && targetShot?.image_settings.aspect && targetShot.image_settings.aspect !== 'inherit' ? targetShot.image_settings.aspect : episodeAspect;
  const defaultLayout = targetShot?.image_settings.layout ?? 'single';
  const readyShot = targetShot && { ...targetShot, video_settings: { resolution: parameters.resolution as '480p' | '720p' | '1080p', duration_ms: parameters.duration_ms } };
  const blockReason = loading ? '正在载入本集创作对象…' : error || (!targetId || !(targetShot || targetAsset) ? '请选择本集创作对象。' : '')
    || capError || (referenceLoading ? '正在核对参考图片…' : referenceError)
    || (kind === 'video' ? readyShot ? videoBlockReason(readyShot, modelId, capabilities, capLoading) : '请选择分镜对象。' : imageGenerationBlockReason(modelId, capabilities, referenceCount, capLoading));
  useEffect(() => { onBlockReason(blockReason); }, [blockReason, onBlockReason]);
  function selectTarget(value: string | undefined) { setTargetId(value); setResolution(undefined); setAspect(undefined); setLayout(undefined); setDuration(undefined); }

  useImperativeHandle(ref, () => ({ prepare: async instructions => {
    if (blockReason) throw new AgentMediaTaskError(blockReason);
    if (!modelId || !targetId || !instructions.trim() || instructions.trim().length > 4000) throw new AgentMediaTaskError('图片和视频生成要求须为 1～4000 字。');
    const [model, caps, episode, latest] = await Promise.all([aiModelConfigs.get(modelId), aiModelConfigs.capabilities(modelId), projectsApi.getEpisode(projectId, episodeId),
      targetKind === 'shot' ? shotsApi.shot(targetId).then(value => value.shot) : assetLibraries.detail(targetId)]);
    if (!model.enabled || model.serviceType !== kind) throw new AgentMediaTaskError('生成模型已变化，请重新选择已启用的模型。');
    const shot = targetKind === 'shot' ? latest as ShotRead : undefined;
    const frozen = settings(shot, episode.aspect);
    let referenceIds: string[] = [];
    if (kind === 'image') {
      const [savedRefs, linked] = await Promise.all([generationReferences(targetKind, targetId).list(), Promise.all((shot?.asset_ids ?? []).map(id => assetLibraries.detail(id)))]);
      referenceIds = [...new Set([...linked.flatMap(asset => asset.state === 'confirmed' && asset.media_id ? [asset.media_id] : []), ...savedRefs.items.map(item => item.media_id)])];
      const reason = imageGenerationBlockReason(modelId, caps, referenceIds.length, false); if (reason) throw new AgentMediaTaskError(reason);
    } else {
      const reason = shot && videoBlockReason({ ...shot, video_settings: { resolution: frozen.resolution as '480p' | '720p' | '1080p', duration_ms: frozen.duration_ms } }, modelId, caps, false);
      if (!shot || reason) throw new AgentMediaTaskError(reason || '请选择可生成视频的分镜。');
      referenceIds = [shot.image!.media_id];
    }
    const task: AgentTaskSpec = { kind, target_id: targetId, instructions: instructions.trim(), model_config_id: modelId, count,
      parameters: { target_kind: targetKind, ...frozen, ...(kind === 'image' ? { reference_media_ids: referenceIds } : {}) } };
    return { task, confirmation: `${targetKind === 'shot' ? shotLabel(latest as ShotRead) : assetLabel(latest as AssetRead)}\n生成模型：${model.name}\n${reviewParameterLabels({ ...frozen, reference_media_ids: referenceIds }).join(' · ')}\n候选数量：${count} ${kind === 'image' ? '张图片' : '段视频'}\n\n生成要求：${instructions.trim()}\n\n确认后会调用协作模型并提交这项生成任务，可能产生模型费用。生成结果先保留为候选，仍需核对后采用。` };
  } }));

  return <section className="agent-media-task" aria-label={kind === 'image' ? '指定图片任务' : '指定视频任务'}>
    <div className="agent-media-task-heading"><strong>{kind === 'image' ? '图片任务' : '视频任务'}</strong><Button type="link" size="small" loading={loading} disabled={disabled} onClick={() => setRevision(value => value + 1)}>刷新对象与能力</Button></div>
    <div className="agent-media-task-fields">
      {kind === 'image' && <label>对象类型<Select aria-label="生成对象类型" value={targetKind} disabled={disabled} options={[{ value: 'asset', label: '角色、场景或道具' }, { value: 'shot', label: '分镜画面' }]} onChange={value => { setTargetKind(value); selectTarget(undefined); }}/></label>}
      <label className="agent-media-task-wide">创作对象<Select aria-label="媒体创作对象" value={targetId} loading={loading} disabled={disabled || loading} showSearch optionFilterProp="label" placeholder={targetKind === 'asset' ? '选择本集素材' : '选择本集镜头'}
        options={targetKind === 'asset' ? assets.map(item => ({ value: item.id, label: assetLabel(item) })) : shots.map(item => ({ value: item.id, label: shotLabel(item), disabled: kind === 'video' && (!item.image || item.image.is_stale) }))} onChange={selectTarget}/></label>
      <label className="agent-media-task-wide">生成模型<EpisodeModelSelect key={kind} kind={kind} value={selection} onChange={setSelection} onResolvedChange={onResolvedModel} disabled={disabled} label={kind === 'image' ? 'Agent 图片生成模型' : 'Agent 视频生成模型'}/></label>
      <label>清晰度<Select aria-label="媒体清晰度" value={resolution ?? 'current'} disabled={disabled} options={[{ value: 'current', label: `沿用目标（${defaultResolution}）` }, ...(kind === 'image' ? imageResolutions : ['480p', '720p', '1080p']).map(value => ({ value, label: value }))]} onChange={value => setResolution(value === 'current' ? undefined : value)}/></label>
      <label>画幅<Select aria-label="媒体画幅" value={aspect ?? 'current'} disabled={disabled} options={[{ value: 'current', label: `沿用设置（${defaultAspect}）` }, ...imageAspects.map(value => ({ value, label: value }))]} onChange={value => setAspect(value === 'current' ? undefined : value)}/></label>
      {kind === 'image' && targetKind === 'shot' && <label>布局<Select aria-label="图片布局" value={layout ?? 'current'} disabled={disabled} options={[{ value: 'current', label: `沿用目标（${imageLayouts.find(item => item.value === defaultLayout)?.label ?? '单图'}）` }, ...imageLayouts.map(({ value, label }) => ({ value, label }))]} onChange={value => setLayout(value === 'current' ? undefined : value)}/></label>}
      {kind === 'video' && <label>时长（秒）<InputNumber aria-label="视频时长（秒）" value={Number(parameters.duration_ms) / 1000} min={1} max={3600} precision={0} disabled={disabled} onChange={value => setDuration(value ?? undefined)}/></label>}
      <label>候选数量<InputNumber aria-label="媒体候选数量" value={count} min={1} max={4} precision={0} disabled={disabled} onChange={value => setCount(value ?? 1)}/></label>
    </div>
    <p className="agent-media-task-help">{kind === 'video' ? '自动使用当前已采用的分镜图作为参考。未采用或已过期的画面须先核对。' : '自动带入对象已有参考图；分镜图同时使用关联素材的已采用图片。'}{targetId && !referenceLoading ? ` 当前参考图片 ${referenceCount} 张。` : ''}</p>
    {blockReason && <Alert type={error || capError || referenceError ? 'error' : 'info'} message={blockReason}/>}
  </section>;
}
