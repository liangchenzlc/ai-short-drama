import { AssetPromptInput } from '@host/features/creation/assets/images/AssetPromptInput';
import { GenerationComposer } from '@host/features/creation/shared/generation/GenerationComposer';
import { ImageGroupGallery } from '@host/features/media/image-groups/imageGroupGallery';
import type { AssetView, ShotView, VideoReference } from '@host/types/creation/workspace/workbenchView';
import type { ShotSize } from '@host/types/creation/storyboard/shots';
import { Button, Checkbox, Input, InputNumber, Popover, Select } from 'antd';
import { ChevronDown, Clapperboard, Plus, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import './storyboardView.css';

type ShotPatch = Partial<Pick<ShotView, 'title' | 'script' | 'shotSize' | 'durationMs' | 'imagePrompt' | 'videoPrompt'>>;

export function StoryboardView({ shot, assets, previewMediaId, imageStatus, videoStatus, candidates,
  onChange, onAssetsChange, onPreview, onAdopt, onSelectGroup, onReferenceChange, onGenerateImage, onGenerateVideo, onAdoptVideo, onRetryItem }: {
  shot: ShotView;
  assets: AssetView[];
  previewMediaId: string | null;
  imageStatus: string;
  videoStatus: string;
  candidates: NonNullable<ShotView['video']>[];
  onChange: (patch: ShotPatch) => void;
  onAssetsChange: (assetIds: string[]) => void;
  onPreview: (mediaId: string) => void;
  onAdopt: (mediaId: string) => void;
  onSelectGroup: (groupId: string) => void;
  onReferenceChange: (reference: VideoReference, selected: boolean) => void;
  onGenerateImage: () => void;
  onGenerateVideo: () => void;
  onAdoptVideo: (mediaId: string) => void;
  onRetryItem?: (itemId: string) => void;
}) {
  const [imageOpen, setImageOpen] = useState(false);
  const imageComposer = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (imageOpen) imageComposer.current?.scrollIntoView({
      block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
    });
  }, [imageOpen]);
  const [videoModel, setVideoModel] = useState<'reference' | 'text'>('reference');
  const [previewVideoId, setPreviewVideoId] = useState<string | null>(null);
  const previewIndex = candidates.findIndex((video) => video.mediaId === previewVideoId);
  const displayedVideo = candidates[previewIndex] ?? shot.video;
  const group = shot.imageGroups.find((item) => item.id === shot.activeGroupId) ?? null;
  const image = group?.items.find((item) => item.media_id === previewMediaId) ?? shot.adoptedImage;
  const associated = assets.filter((asset) => shot.assetIds.includes(asset.id));
  const imageReferences = associated.filter((asset) => asset.adoptedImage)
    .map((asset) => ({ ...asset.adoptedImage!, name: asset.name }));
  const selectedAssets = shot.videoReferences.filter((reference) => reference.type === 'asset')
    .map((reference) => associated.find((asset) => asset.id === reference.assetId)).filter((asset) => !!asset);
  const shotReference = shot.videoReferences.find((reference) => reference.type === 'shotImage');
  const referenceCount = countShotVideoReferences(shot, assets);
  const blocker = videoModel === 'text' && referenceCount ? '当前模型仅支持文字，请移除参考图片或更换模型。' :
    referenceCount > 4 ? '当前模型最多支持 4 张参考图片，请移除多余图片。' : '';

  return (
    <section className="storyboardView" aria-label="分镜详情">
      <header className="storyboardViewHeader"><h2>镜头 {String(shot.position).padStart(2, '0')}</h2><span>{shot.title}</span></header>
      <div className="storyboardViewBody">
        <div className="storyboardDetails">
          <h3>镜头信息</h3>
          <label>名称<Input aria-label="镜头名称" value={shot.title} onChange={(event) => onChange({ title: event.target.value })} /></label>
          <div className="storyboardFieldRow">
            <label>景别<Select aria-label="景别" value={shot.shotSize} onChange={(shotSize: ShotSize) => onChange({ shotSize })}
              options={[
                { value: 'wide_shot', label: '远景' }, { value: 'full_shot', label: '全景' },
                { value: 'medium_shot', label: '中景' }, { value: 'close_up', label: '特写' },
                { value: 'extreme_close_up', label: '大特写' }, { value: 'two_shot', label: '双人镜头' },
              ]} /></label>
            <label>时长（秒）<InputNumber aria-label="时长（秒）" min={1} value={shot.durationMs / 1000}
              onChange={(value) => onChange({ durationMs: (value ?? 1) * 1000 })} /></label>
          </div>
          <label>镜头描述<Input.TextArea aria-label="镜头描述" autoSize={{ minRows: 3 }} value={shot.script}
            onChange={(event) => onChange({ script: event.target.value })} /></label>
          <div className="shotAssociated"><span>关联资产</span><div>{associated.map((asset) => <span key={asset.id}>{asset.name}</span>)}
            <Popover trigger="click" title="关联资产" content={<div className="shotAssetPicker">
              {assets.map((asset) => <Checkbox key={asset.id} aria-label={'关联资产：' + (asset.name || '未命名素材')}
                checked={shot.assetIds.includes(asset.id)} onChange={(event) => onAssetsChange(event.target.checked ?
                  [...shot.assetIds, asset.id] : shot.assetIds.filter((id) => id !== asset.id))}>
                {asset.name || '未命名素材'}
              </Checkbox>)}
            </div>}>
              <Button size="small" type="text" aria-label="关联资产" icon={<Plus size={15} />} />
            </Popover>
          </div></div>
          <div className="shotImageHeading"><h3>分镜图</h3>
            {shot.imageGroups.length > 1 && <Select aria-label="分镜图片组" value={shot.activeGroupId}
              onChange={onSelectGroup} options={shot.imageGroups.map((item, index) => ({
                value: item.id, label: index === 0 ? '最新生成' : '历史图片 ' + index,
              }))} />}
          </div>
          <div className="shotImageGallery"><ImageGroupGallery group={group} adoptedImage={shot.adoptedImage}
            previewMediaId={previewMediaId} onPreview={onPreview} onAdopt={onAdopt} onRetryItem={onRetryItem} /></div>
          {image?.media_id && <Button size="small" onClick={() => onReferenceChange({ type: 'shotImage', mediaId: image.media_id! }, true)}>
            使用分镜图作为视频参考
          </Button>}
          <Button className="shotImageToggle" type="text" aria-label={imageOpen ? '收起分镜图生成' : '展开分镜图生成'}
            aria-expanded={imageOpen} icon={<ChevronDown size={15} className={imageOpen ? 'isOpen' : ''} />}
            onClick={() => setImageOpen(!imageOpen)}>生成分镜图</Button>
          {imageOpen && <div ref={imageComposer} className="shotImageComposer"><GenerationComposer prompt={shot.imagePrompt} onPrompt={(imagePrompt) => onChange({ imagePrompt })}
            model={<span>演示图片模型</span>} sendDisabled={false} busy={false} onSend={onGenerateImage}
            promptInput={(id) => <AssetPromptInput id={id} ariaLabel="分镜图片提示词" value={shot.imagePrompt}
              references={imageReferences} onChange={(imagePrompt) => onChange({ imagePrompt })} />} /></div>}
          {imageStatus && <span role="status" aria-label="分镜图生成状态">{imageStatus}</span>}
        </div>
        <div className="storyboardVideo">
          <h3>视频</h3>
          <div className="shotVideoPreview">
            {displayedVideo ? <video key={displayedVideo.mediaId} src={displayedVideo.url} controls preload="metadata" /> :
              <div><Clapperboard size={26} /><span>暂无视频</span></div>}
          </div>
          {videoStatus && <span role="status" aria-label="视频生成状态">{videoStatus}</span>}
          {previewIndex >= 0 && <span>正在预览候选视频 {previewIndex + 1}</span>}
          {candidates.length > 0 && <div className="shotCandidates">
            {candidates.map((video, index) => <div key={video.mediaId}>
              <button type="button" aria-label={'预览候选视频 ' + (index + 1)} onClick={() => setPreviewVideoId(video.mediaId)}>
                候选视频 {index + 1}
              </button>
              <Button size="small" type="text" aria-label={'采用候选视频 ' + (index + 1)}
                onClick={() => { onAdoptVideo(video.mediaId); setPreviewVideoId(null); }}>采用</Button>
            </div>)}
          </div>}
          <div className="shotVideoReferences"><span>视频参考</span>
            {selectedAssets.map((asset) => <div className="shotReference" key={asset.id}>
              {asset.adoptedImage?.url && <img src={asset.adoptedImage.url} alt="" />}
              <span>{asset.name}{asset.adoptedImage ? '' : ' · 缺图'}</span>
              <button type="button" aria-label={'移除参考：' + asset.name}
                onClick={() => onReferenceChange({ type: 'asset', assetId: asset.id }, false)}><X size={12} /></button>
            </div>)}
            {associated.filter((asset) => !asset.adoptedImage && !selectedAssets.some((selected) => selected.id === asset.id)).map((asset) =>
              <span key={asset.id}>{asset.name} · 缺图</span>)}
            {shotReference && <div className="shotReference"><span>分镜图</span>
              <button type="button" aria-label="移除参考：分镜图"
                onClick={() => onReferenceChange(shotReference, false)}><X size={12} /></button></div>}
            <Popover trigger="click" title="添加视频参考" content={<div className="shotReferenceMenu">
              {associated.filter((asset) => asset.adoptedImage && !selectedAssets.some((selected) => selected.id === asset.id))
                .map((asset) => <button type="button" key={asset.id}
                  onClick={() => onReferenceChange({ type: 'asset', assetId: asset.id }, true)}>
                  {asset.adoptedImage?.url && <img src={asset.adoptedImage.url} alt="" />}{asset.name}</button>)}
            </div>}>
              <Button size="small" type="text" aria-label="添加视频参考" icon={<Plus size={15} />} />
            </Popover>
          </div>
          <GenerationComposer purpose="video" prompt={shot.videoPrompt} onPrompt={(videoPrompt) => onChange({ videoPrompt })}
            model={<Select aria-label="视频模型" value={videoModel} onChange={setVideoModel} options={[
              { value: 'reference', label: '演示视频模型' }, { value: 'text', label: '纯文字模型' },
            ]} />} sendDisabled={!!blocker} sendTitle={blocker || undefined} busy={false}
            blocker={blocker ? { message: blocker } : undefined} onSend={onGenerateVideo} />
        </div>
      </div>
    </section>
  );
}

export function countShotVideoReferences(shot: ShotView, assets: AssetView[]) {
  return shot.videoReferences.filter((reference) => reference.type === 'shotImage' ||
    (shot.assetIds.includes(reference.assetId) && assets.some((asset) => asset.id === reference.assetId && asset.adoptedImage))).length;
}
