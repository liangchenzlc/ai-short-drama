import { ScriptSceneEditor } from '@host/features/creation/writing/editor/ScriptSceneEditor';
import { episodeLabel } from '@host/features/creation/workspace/layout/episodeSelector';
import { Dialog } from '@host/shared/ui/Dialog';
import type { EpisodeView, StoryView } from '@host/types/creation/workspace/workbenchView';
import { Button, Input } from 'antd';
import { Plus, Sparkles } from 'lucide-react';
import { useState } from 'react';
import './script-desk.css';
import './writingView.css';

type OutlineDraft = Pick<EpisodeView, 'title' | 'summary'>;
type EpisodePatch = Partial<Pick<EpisodeView, 'title' | 'summary' | 'script'>>;

export function WritingView({ story, episodes, selectedId, outlineDraft, onStoryChange, onEpisodeChange,
  onAdd, onGenerateOutlines, onEditOutline, onApplyOutlines, onDiscardOutlines }: {
  story: StoryView;
  episodes: EpisodeView[];
  selectedId: string;
  outlineDraft: OutlineDraft[] | null;
  onStoryChange: (patch: Partial<StoryView>) => void;
  onEpisodeChange: (id: string, patch: EpisodePatch) => void;
  onAdd: () => void;
  onGenerateOutlines: (count: number) => void;
  onEditOutline: (index: number, patch: Partial<OutlineDraft>) => void;
  onApplyOutlines: () => void;
  onDiscardOutlines: () => void;
}) {
  const [tab, setTab] = useState<'outline' | 'script'>('outline');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [count, setCount] = useState(3);
  const episode = episodes.find((item) => item.id === selectedId) ?? episodes[0];
  const closeDialog = () => { setDialogOpen(false); onDiscardOutlines(); };

  return (
    <section className="writingView" aria-label="剧本创作">
      <header className="writingViewHeader">
        <div className="writingTabs" role="tablist" aria-label="剧本创作内容">
          <button type="button" role="tab" aria-selected={tab === 'outline'} onClick={() => setTab('outline')}>大纲</button>
          <button type="button" role="tab" aria-selected={tab === 'script'} onClick={() => setTab('script')}>剧本</button>
        </div>
        <Button icon={<Plus size={14} />} onClick={onAdd}>添加分集</Button>
      </header>
      {tab === 'outline' ? (
        <div className="writingOutline">
          <div className="writingOutlineInner">
            <div className="writingOutlineTitle"><h1>故事大纲</h1>
              <Button icon={<Sparkles size={14} />} onClick={() => setDialogOpen(true)}>生成分集概要</Button>
            </div>
            <label>题材<Input aria-label="题材" value={story.genre} onChange={(event) => onStoryChange({ genre: event.target.value })} /></label>
            <label>故事核心<Input.TextArea aria-label="故事核心" autoSize={{ minRows: 2 }} value={story.core}
              onChange={(event) => onStoryChange({ core: event.target.value })} /></label>
            <label>大纲正文<Input.TextArea aria-label="大纲正文" autoSize={{ minRows: 5 }} value={story.outline}
              placeholder="写下故事的起承转合" onChange={(event) => onStoryChange({ outline: event.target.value })} /></label>
            {episodes.length > 1 && <div className="writingEpisodes">
              <div className="writingSectionTitle"><h2>分集概要</h2><span>{episodes.length} 集</span></div>
              <div role="list" aria-label="分集概要">
                {episodes.map((item, index) => <div role="listitem" className="writingEpisode" key={item.id}>
                  <strong>{episodeLabel(index)}</strong>
                  <Input.TextArea aria-label={episodeLabel(index) + '概要'} autoSize={{ minRows: 2 }}
                    value={item.summary} placeholder="填写本集概要"
                    onChange={(event) => onEpisodeChange(item.id, { summary: event.target.value })} />
                </div>)}
              </div>
            </div>}
          </div>
        </div>
      ) : (
        <div className="writingScript sd-desk">
          <div className="sd-doc">
            <ScriptSceneEditor key={episode.id} script={episode.script} readOnly={false} rewriteDisabled={false} loaded
              frozenSelection={null} onChange={(script) => onEpisodeChange(episode.id, { script })}
              onRewrite={(selection) => onEpisodeChange(episode.id, {
                script: selection.source.slice(0, selection.start) + selection.source.slice(selection.start, selection.end) +
                  '（改写）' + selection.source.slice(selection.end),
              })} />
          </div>
        </div>
      )}
      {dialogOpen && <Dialog title="生成分集概要" onClose={closeDialog}>
        <div className="outlineDialog">
          <label>生成集数<Input type="number" aria-label="生成集数" min={1} max={100} value={count}
            onChange={(event) => setCount(Number(event.target.value))} /></label>
          <Button onClick={() => onGenerateOutlines(Math.max(1, Math.min(100, count || 1)))}>生成概要</Button>
          {outlineDraft && <>
            <p>{episodes[0].summary.trim() ? '将新增 ' + outlineDraft.length + ' 集' :
              '填写首集，新增 ' + (outlineDraft.length - 1) + ' 集'}</p>
            <div className="outlineDrafts">{outlineDraft.map((item, index) =>
              <label key={index}>{'概要 ' + (index + 1)}
                <Input.TextArea aria-label={'生成概要 ' + (index + 1)} value={item.summary}
                  onChange={(event) => onEditOutline(index, { summary: event.target.value })} />
              </label>)}</div>
            <div className="outlineDialogActions"><Button onClick={closeDialog}>取消</Button>
              <Button type="primary" onClick={() => { onApplyOutlines(); setDialogOpen(false); }}>采用概要</Button></div>
          </>}
        </div>
      </Dialog>}
    </section>
  );
}