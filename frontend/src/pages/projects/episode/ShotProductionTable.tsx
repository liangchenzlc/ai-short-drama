import { Button } from 'antd';
import type { EpisodeWorkflow, ShotItem } from '../../../features/projects/episode-workflow';
import { hasAvailableMedia, mediaUrl, type ListedMedia } from '../../../features/projects/episode-media';
import { ImagePreview } from './ImagePreview';
import { Icon } from '../../../components/ui/Icon';
import { imageSettingsForShot } from '../../../features/projects/shot-generation-settings';
import { ImageAdvancedSettings, VideoAdvancedSettings } from './ShotAdvancedSettings';

export function ShotProductionTable({ value, shot, shotLabel, readOnly, onChange, projectId, mediaItems }: {
  value: EpisodeWorkflow;
  shot: ShotItem;
  shotLabel: string;
  readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectId?: string;
  mediaItems: readonly ListedMedia[];
}) {
  const frame = shot.firstFrames.find((item) => item.id === shot.selectedFirstId);
  const video = shot.videos.find((item) => item.id === shot.selectedVideoId);
  const settings = imageSettingsForShot(shot);
  const imageAspect = frame || settings.aspect === 'inherit' ? value.aspect : settings.aspect;
  const videoSrc = projectId && video?.value.kind === 'project-video' && hasAvailableMedia(mediaItems, video.value.id, 'video/mp4') ? mediaUrl(projectId, video.value.id) : null;
  const update = (next: EpisodeWorkflow) => { if (!readOnly) onChange(next); };
  return <>
    <section className="storyboard-output storyboard-image-panel" aria-label={`${shotLabel}分镜图`}>
      <h3>分镜图</h3>
      <div className="storyboard-preview-area">
        <div className="storyboard-image-placeholder" style={{ aspectRatio: imageAspect.replace(':', ' / ') }}>
          {frame ? <ImagePreview media={frame.value} label={`${shotLabel}分镜图`} projectId={projectId} mediaItems={mediaItems} /> : <div className="storyboard-media-empty"><Icon name="scene" size={28} /><span>暂无分镜图</span></div>}
        </div>
      </div>
      {frame && shot.frameReview === 'stale' && <p className="storyboard-result-note">分镜脚本已修改，请重新核对当前图片。</p>}
      <ImageAdvancedSettings value={value} shot={shot} readOnly={readOnly} onChange={update} />
      <div className="storyboard-generation-actions"><Button disabled aria-describedby={`image-note-${shot.id}`}>生成分镜图</Button><p id={`image-note-${shot.id}`}>{readOnly ? '当前为只读模式' : '图片生成暂未开放'}</p></div>
    </section>
    <section className="storyboard-output storyboard-video-panel" aria-label={`${shotLabel}分镜视频`}>
      <h3>分镜视频</h3>
      <div className="storyboard-preview-area">
        <div className="storyboard-video-placeholder" style={{ aspectRatio: value.aspect.replace(':', ' / ') }}>
          {videoSrc ? <video controls preload="metadata" src={videoSrc} aria-label={`${shotLabel}分镜视频`} /> : <div className="storyboard-media-empty"><Icon name="film" size={28} /><span>{video ? '视频暂不可预览' : '暂无分镜视频'}</span>{video && <small>{video.value.kind === 'demo-motion' ? '已保留演示动效记录' : '已保留视频记录，当前媒体不可用'}</small>}</div>}
        </div>
      </div>
      {video && shot.videoReview === 'stale' && <p className="storyboard-result-note">制作内容已修改，请重新核对当前视频。</p>}
      <VideoAdvancedSettings value={value} shot={shot} readOnly={readOnly} onChange={update} />
      <div className="storyboard-generation-actions"><Button disabled aria-describedby={`video-note-${shot.id}`}>生成分镜视频</Button><p id={`video-note-${shot.id}`}>{readOnly ? '当前为只读模式' : '视频生成暂未开放'}</p></div>
    </section>
  </>;
}
