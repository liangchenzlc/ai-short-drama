import { BatchPreview } from '@host/features/generations/batches/batchPreview';
import { BatchToolbar } from '@host/features/generations/batches/batchToolbar';
import { useBatchSelectionState } from '@host/features/generations/batches/useBatchSelectionState';
import { CreativeAssistantPanel } from '@host/features/assistant/panel/CreativeAssistantPanel';
import { AssetView } from '@host/features/creation/assets/editor/assetView';
import { ResourceCollection } from '@host/features/creation/shared/resource-view/resourceCollection';
import { StoryboardView } from '@host/features/creation/storyboard/workbench/storyboardView';
import { CreationEditorSlot, CreationWorkspace } from '@host/features/creation/workspace/layout/CreationWorkspace';
import { EpisodeSelector } from '@host/features/creation/workspace/layout/episodeSelector';
import { TopStepNav } from '@host/features/creation/workspace/navigation/TopStepNav';
import { WritingView } from '@host/features/creation/writing/editor/writingView';
import type { ResourceView } from '@host/types/creation/shared/resource-view/ResourceViewToggle';
import type { CreationStage } from '@host/types/creation/workspace/model';
import type { BatchItemView } from '@host/types/creation/workspace/workbenchView';
import type { BatchScene } from '@host/types/generations/batches/requests';
import { Button, Input, Select } from 'antd';
import { Sparkles } from 'lucide-react';
import { useState } from 'react';
import { useWorkbenchPreviewState } from './workbenchPreviewState';

export function WorkbenchPreviewApp() {
  const state = useWorkbenchPreviewState();
  const [stage, setStage] = useState<CreationStage>('source');
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assetMode, setAssetMode] = useState<ResourceView>('single');
  const [shotMode, setShotMode] = useState<ResourceView>('single');
  const assetSelection = useBatchSelectionState('assets');
  const shotSelection = useBatchSelectionState(state.episodeId);
  const assetSelected = assetSelection.ids;
  const shotSelected = shotSelection.ids;
  const [batchDialog, setBatchDialog] = useState<BatchScene | null>(null);
  const [assetQuery, setAssetQuery] = useState('');
  const [shotQuery, setShotQuery] = useState('');
  const [tag, setTag] = useState<string>();
  const [kind, setKind] = useState<string>();


  const asset = state.assets.find((item) => item.id === state.assetId) ?? state.assets[0];
  const filteredAssets = state.assets.filter((item) => (!kind || item.kind === kind) && (!tag || item.tags.includes(tag)) &&
    (item.name + item.description).toLowerCase().includes(assetQuery.toLowerCase()));
  const episodeShots = state.shots.filter((item) => item.episodeId === state.episodeId);
  const filteredShots = episodeShots.filter((item) => (item.title + item.script).toLowerCase().includes(shotQuery.toLowerCase()));
  const shot = episodeShots.find((item) => item.id === state.shotId) ?? episodeShots[0];
  const availableReferences = state.assets.filter((item) => item.id !== asset.id && item.adoptedImage)
    .map((item) => ({ ...item.adoptedImage!, name: item.name }));
  const assetBatch = state.batches.find((item) => item.scene === 'asset_image');
  const shotBatch = state.batches.find((item) => item.scene !== 'asset_image' && item.episodeId === state.episodeId);
  const dialogSelection = batchDialog === 'asset_image' ? assetSelected : shotSelected;
  const missingIds = dialogSelection.filter((id) => {
    if (batchDialog === 'asset_image') return !state.assets.find((item) => item.id === id)?.adoptedImage;
    const item = state.shots.find((value) => value.id === id);
    return batchDialog === 'shot_image' ? !item?.adoptedImage : !item?.video;
  });

  return (
    <div className="creation-page web-episode workbenchPreview">
      <TopStepNav currentStage={stage} onStageChange={setStage} />
      <header className="creation-top">
        <div className="creation-project-location">
          <span className="creation-project-name">未寄出的信</span>
          {stage !== 'assembly' && <EpisodeSelector episodes={state.episodes} selectedId={state.episodeId}
            onSelect={state.setEpisodeId} onAdd={state.addEpisode}
            onRename={(id, title) => state.changeEpisode(id, { title })} onMove={state.moveEpisode} onDelete={state.deleteEpisode} />}
          <span role="status" aria-label="演示数据">演示数据 · 刷新重置</span>
        </div>
        <div className="creation-project-actions">
          <Button className="creation-assistant-entry" aria-label="AI 创作助手" aria-expanded={assistantOpen}
            icon={<Sparkles size={14} />} onClick={() => setAssistantOpen(!assistantOpen)}>AI 创作助手</Button>
        </div>
      </header>
      <div className="creation-layout">
        <CreationWorkspace stage={stage} assistantOpen={assistantOpen} workRequest={0} subject={null} onSubject={() => {}}
          assistantPanel={<CreativeAssistantPanel layout="standard" projectId="1" projectTitle="未寄出的信" open={assistantOpen}
            readOnly onClose={() => setAssistantOpen(false)} onOpenModelSettings={() => {}} prepareContext={async () => null} />}>
          {stage === 'source' && <WritingView story={state.story} episodes={state.episodes} selectedId={state.episodeId}
            outlineDraft={state.outlineDraft} onStoryChange={(patch) => state.setStory((current) => ({ ...current, ...patch }))}
            onEpisodeChange={state.changeEpisode} onAdd={state.addEpisode} onGenerateOutlines={state.generateOutlines}
            onEditOutline={(index, patch) => state.setOutlineDraft((current) =>
              current?.map((item, position) => position === index ? { ...item, ...patch } : item) ?? null)}
            onApplyOutlines={state.applyOutlines} onDiscardOutlines={() => state.setOutlineDraft(null)} />}
          {stage === 'assets' && <>
            <aside className="workbenchResourceRail" aria-label="资产列表">
              <h2>资产生成</h2>
              {filteredAssets.map((item) => <button type="button" key={item.id} aria-current={state.assetId === item.id}
                onClick={() => { state.setAssetId(item.id); setAssetMode('single'); }}>{item.name}</button>)}
            </aside>
            <CreationEditorSlot stage="assets"><ResourceCollection title="资产工作区" mode={assetMode}
              selectedIds={assetSelected} onMode={setAssetMode} onToggle={assetSelection.toggle}
              onOpen={(id) => { state.setAssetId(id); setAssetMode('single'); }}
              toolbar={<BatchToolbar filteredIds={filteredAssets.map((item) => item.id)} selectedIds={assetSelected}
                items={assetBatch?.items ?? []} scenes={['asset_image']} onSelect={assetSelection.setIds}
                onLaunch={setBatchDialog} onRetry={() => assetBatch && state.retryBatch(assetBatch.id)} />}
              filters={<><Input aria-label="搜索资产" placeholder="搜索资产" value={assetQuery}
                onChange={(event) => setAssetQuery(event.target.value)} allowClear />
                <Select aria-label="资产类型" placeholder="全部类型" value={kind} allowClear onChange={setKind}
                  options={[{ value: 'character', label: '角色' }, { value: 'scene', label: '场景' }, { value: 'prop', label: '道具' }]} />
                <Select aria-label="标签筛选" placeholder="全部标签" value={tag} allowClear showSearch
                  onChange={setTag} options={[...new Set(state.assets.flatMap((item) => item.tags))]
                    .map((value) => ({ value, label: value }))} /></>}
              items={filteredAssets.map((item) => ({ id: item.id, label: item.name, selectionLabel: '选择资产：' + item.name,
                description: item.description, image: item.adoptedImage?.url ?? null, status: batchStatus(assetBatch?.items ?? [], item.id) || (item.adoptedImage ? '已生成' : '未生成') }))}>
              <AssetView asset={asset} previewMediaId={state.previewByAsset[asset.id] ?? asset.adoptedImage?.media_id ?? null}
                references={state.assetReferences[asset.id] ?? []} availableReferences={availableReferences}
                onChange={(patch) => state.changeAsset(asset.id, patch)}
                onPreview={(id) => state.setPreviewByAsset((current) => ({ ...current, [asset.id]: id }))}
                onAdopt={(id) => state.adoptAssetImage(asset.id, id)} onSelectGroup={(id) => state.selectAssetGroup(asset.id, id)}
                onReferencesChange={(references) => state.changeAssetReferences(asset.id, references)}
                onGenerate={() => state.generateAssetImage(asset.id)} />
            </ResourceCollection></CreationEditorSlot>
          </>}
          {stage === 'storyboard' && <>
            <aside className="workbenchResourceRail" aria-label="分镜列表">
              <h2>分镜制作</h2>
              {filteredShots.map((item) => <button type="button" key={item.id} aria-current={item.id === shot?.id}
                onClick={() => { state.setShotId(item.id); setShotMode('single'); }}>
                镜头 {String(item.position).padStart(2, '0')}
              </button>)}
            </aside>
            <CreationEditorSlot stage="storyboard"><ResourceCollection title="分镜工作区" mode={shotMode}
              selectedIds={shotSelected} onMode={setShotMode} onToggle={shotSelection.toggle}
              onOpen={(id) => { state.setShotId(id); setShotMode('single'); }}
              toolbar={<BatchToolbar filteredIds={filteredShots.map((item) => item.id)} selectedIds={shotSelected}
                items={shotBatch?.items ?? []} scenes={['shot_image', 'shot_video']} onSelect={shotSelection.setIds}
                onLaunch={setBatchDialog} onRetry={() => shotBatch && state.retryBatch(shotBatch.id)} />}
              filters={<Input aria-label="搜索分镜" placeholder="搜索分镜" value={shotQuery}
                onChange={(event) => setShotQuery(event.target.value)} allowClear />}
              items={filteredShots.map((item) => {
                const number = String(item.position).padStart(2, '0');
                return { id: item.id, label: '镜头 ' + number, selectionLabel: '选择分镜：' + number,
                  description: item.title + ' · ' + item.script, image: item.adoptedImage?.url ?? null,
                  status: (batchStatus(shotBatch?.items ?? [], item.id) ? batchStatus(shotBatch?.items ?? [], item.id) + ' · ' : '') + ({ wide_shot: '远景', full_shot: '全景', medium_shot: '中景', close_up: '特写',
                    extreme_close_up: '大特写', two_shot: '双人镜头' }[item.shotSize ?? 'medium_shot']) +
                    ' · ' + item.durationMs / 1000 + ' 秒 · ' +
                    (item.adoptedImage ? '分镜图已生成' : '无分镜图') + ' · ' + (item.video ? '视频已生成' : '无视频') };
              })}>
              {shot ? <StoryboardView shot={shot} assets={state.assets}
                previewMediaId={state.previewByShot[shot.id] ?? shot.adoptedImage?.media_id ?? null}
                imageStatus={state.imageStatuses[shot.id] ?? ''} videoStatus={state.videoStatuses[shot.id] ?? ''}
                candidates={state.videoCandidates[shot.id] ?? []} onChange={(patch) => state.changeShot(shot.id, patch)}
                onPreview={(id) => state.setPreviewByShot((current) => ({ ...current, [shot.id]: id }))}
                onAdopt={(id) => state.adoptShotImage(shot.id, id)} onSelectGroup={(id) => state.selectShotGroup(shot.id, id)}
                onReferenceChange={(reference, selected) => state.changeVideoReference(shot.id, reference, selected)}
                onGenerateImage={() => state.generateShotImage(shot.id)}
                onGenerateVideo={() => state.generateShotVideo(shot.id)} onAdoptVideo={(id) => state.adoptShotVideo(shot.id, id)} /> :
                <div className="workbenchEmptyPanel">本集暂无镜头</div>}
            </ResourceCollection></CreationEditorSlot>
          </>}
          {stage === 'assembly' && <div className="workbenchEmptyPanel">本次不预览成片</div>}
        </CreationWorkspace>
      </div>
      {batchDialog && <BatchPreview scene={batchDialog} selectedIds={dialogSelection} missingIds={missingIds}
        onClose={() => setBatchDialog(null)} onConfirm={(ids) => { state.startBatch(batchDialog, ids); setBatchDialog(null); }} />}
    </div>
  );
}

function batchStatus(items: BatchItemView[], targetId: string) {
  const item = items.find((entry) => entry.targetId === targetId);
  if (!item) return '';
  return ({ idle: '待生成', queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败：' + item.error })[item.status];
}