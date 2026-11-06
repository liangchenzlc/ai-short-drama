import { AssetView } from '@host/features/creation/assets/editor/assetView';
import { CreativeAssistantPanel } from '@host/features/assistant/panel/CreativeAssistantPanel';
import { CreationEditorSlot, CreationWorkspace } from '@host/features/creation/workspace/layout/CreationWorkspace';
import { EpisodeSelector } from '@host/features/creation/workspace/layout/episodeSelector';
import { WritingView } from '@host/features/creation/writing/editor/writingView';
import { TopStepNav } from '@host/features/creation/workspace/navigation/TopStepNav';
import type { CreationStage } from '@host/types/creation/workspace/model';
import { Button } from 'antd';
import { Sparkles } from 'lucide-react';
import { useState } from 'react';
import { useWorkbenchPreviewState } from './workbenchPreviewState';

export function WorkbenchPreviewApp() {
  const state = useWorkbenchPreviewState();
  const [stage, setStage] = useState<CreationStage>('source');
  const [assistantOpen, setAssistantOpen] = useState(false);
  const asset = state.assets.find((item) => item.id === state.assetId) ?? state.assets[0];
  const availableReferences = state.assets.filter((item) => item.id !== asset.id && item.adoptedImage)
    .map((item) => ({ ...item.adoptedImage!, name: item.name }));

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
              {state.assets.map((asset) => <button type="button" key={asset.id} aria-current={state.assetId === asset.id}
                onClick={() => state.setAssetId(asset.id)}>{asset.name}</button>)}
            </aside>
            <CreationEditorSlot stage="assets"><AssetView asset={asset}
              previewMediaId={state.previewByAsset[asset.id] ?? asset.adoptedImage?.media_id ?? null}
              references={state.assetReferences[asset.id] ?? []} availableReferences={availableReferences}
              onChange={(patch) => state.changeAsset(asset.id, patch)}
              onPreview={(id) => state.setPreviewByAsset((current) => ({ ...current, [asset.id]: id }))}
              onAdopt={(id) => state.adoptAssetImage(asset.id, id)}
              onSelectGroup={(id) => state.selectAssetGroup(asset.id, id)}
              onReferencesChange={(references) => state.changeAssetReferences(asset.id, references)}
              onGenerate={() => state.generateAssetImage(asset.id)} /></CreationEditorSlot>
          </>}
          {stage === 'storyboard' && <>
            <aside className="workbenchResourceRail" aria-label="分镜列表">
              <h2>分镜制作</h2>
              {state.shots.filter((shot) => shot.episodeId === state.episodeId).map((shot) =>
                <button type="button" key={shot.id} onClick={() => state.setShotId(shot.id)}>
                  镜头 {String(shot.position).padStart(2, '0')}
                </button>)}
            </aside>
            <CreationEditorSlot stage="storyboard"><div className="workbenchEmptyPanel">选择镜头，开始创作</div></CreationEditorSlot>
          </>}
          {stage === 'assembly' && <div className="workbenchEmptyPanel">本次不预览成片</div>}
        </CreationWorkspace>
      </div>
    </div>
  );
}