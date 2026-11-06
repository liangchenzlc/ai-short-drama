import { ResourceViewToggle } from '@host/features/creation/shared/resource-view/ResourceViewToggle';
import { StoryboardShotCard } from '@host/features/creation/storyboard/list/StoryboardShotCard';
import '@host/features/creation/storyboard/list/index.css';
import { Icon } from '@host/shared/ui/Icon';
import type { ShotRead } from '@host/types/creation/storyboard/shots';
import type { ResourceView } from '@host/types/creation/shared/resource-view/ResourceViewToggle';
import type { ShotView } from '@host/types/creation/workspace/workbenchView';
import { Button, Input } from 'antd';
import { History } from 'lucide-react';

export function WorkbenchPreviewShotRail({ shots, visible, selectedId, query, pendingOnly, view, onQuery, onPending,
  onView, onOpen, onCreate, onDelete, onHistory }: {
  shots: ShotView[];
  visible: ShotView[];
  selectedId?: string;
  query: string;
  pendingOnly: boolean;
  view: ResourceView;
  onQuery: (value: string) => void;
  onPending: (value: boolean) => void;
  onView: (view: ResourceView) => void;
  onOpen: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  onHistory: () => void;
}) {
  return (
    <div className="storyboard-workspace" style={{ height: '100%' }}>
      <section className={'standard-storyboard-list resource-view-' + view} aria-label="分镜创作区域">
        <header className="storyboard-editor-heading">
          <h3>分镜表 <Button className="storyboard-list-count" type="text" aria-label="分镜生成记录" onClick={onHistory}>{shots.length}</Button></h3>
          <div className="storyboard-heading-actions">
            <ResourceViewToggle value={view} onChange={onView} options={['list', 'grid']} />
            <Button type="text" aria-label="查看分镜提取记录" title="分镜提取记录" icon={<History size={16} />} onClick={onHistory} />
          </div>
        </header>
        <div className="standard-storyboard-filters">
          <Input prefix={<Icon name="search" size={14} />} aria-label="搜索镜头名称" value={query}
            placeholder="搜索镜头名称" allowClear onChange={(event) => onQuery(event.target.value)} />
          <div className="storyboard-category-tabs" aria-label="镜头筛选">
            <button type="button" aria-pressed={!pendingOnly} onClick={() => onPending(false)}>全部镜头</button>
            <button type="button" aria-pressed={pendingOnly} onClick={() => onPending(true)}>待制作</button>
          </div>
        </div>
        <div className="storyboard-list lazy-scroll">
          {visible.map((shot) => <StoryboardShotCard key={shot.id} shot={asShotRead(shot)} selected={selectedId === shot.id}
            disabled={false} batch={false} checked={false} onSelect={() => onOpen(shot.id)} onCheck={() => {}}
            onDelete={() => onDelete(shot.id)} />)}
          {!visible.length && <div className="studio-empty"><p>{shots.length ? '没有匹配的镜头' : '还没有镜头'}</p></div>}
        </div>
        <Button className="resource-create-button" type="text" aria-label="创建分镜"
          icon={<Icon name="plus" size={16} />} onClick={onCreate}>创建</Button>
      </section>
    </div>
  );
}

function asShotRead(shot: ShotView): ShotRead {
  const image = shot.adoptedImage;
  return {
    id: shot.id, row_version: '1', context_hash: '', image_settings: { layout: 'single', aspect: null, resolution: '1K' },
    position: shot.position, title: shot.title, shot_size: shot.shotSize, script: shot.script, duration_ms: shot.durationMs,
    source_excerpt: '', asset_ids: shot.assetIds, deleted_at: null, video_prompt: shot.videoPrompt,
    video_default_prompt: '', video_system_prompt: '', video_settings: { resolution: '720p' }, video_context_hash: '',
    image: image?.url ? { media_id: image.media_id, media_asset_id: null, url: image.url, width: image.width,
      height: image.height, layout: 'single', aspect: '16:9', resolution: '1K', is_stale: false } : null,
    video: shot.video ? { media_id: shot.video.mediaId, media_asset_id: null, url: shot.video.url,
      resolution: '720p', duration_ms: shot.durationMs, first_frame_media_id: null, is_stale: false } : null,
  };
}