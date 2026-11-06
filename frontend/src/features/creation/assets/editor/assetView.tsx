import { AssetPromptInput } from '@host/features/creation/assets/images/AssetPromptInput';
import { GenerationComposer } from '@host/features/creation/shared/generation/GenerationComposer';
import { ImageGroupGallery } from '@host/features/media/image-groups/imageGroupGallery';
import type { AssetPromptReference } from '@host/types/creation/assets/images/asset-prompt-references';
import type { AssetView as AssetViewData, CharacterProfile } from '@host/types/creation/workspace/workbenchView';
import { Button, Input, Popover, Select } from 'antd';
import { Plus, X } from 'lucide-react';
import './assetView.css';

type AssetPatch = Partial<Pick<AssetViewData, 'name' | 'description' | 'tags' | 'profile' | 'imagePrompt'>>;

export function AssetView({ asset, previewMediaId, references, availableReferences, onChange, onPreview, onAdopt,
  onSelectGroup, onReferencesChange, onGenerate }: {
  asset: AssetViewData;
  previewMediaId: string | null;
  references: AssetPromptReference[];
  availableReferences: AssetPromptReference[];
  onChange: (patch: AssetPatch) => void;
  onPreview: (mediaId: string) => void;
  onAdopt: (mediaId: string) => void;
  onSelectGroup: (groupId: string) => void;
  onReferencesChange: (references: AssetPromptReference[]) => void;
  onGenerate: () => void;
}) {
  const group = asset.imageGroups.find((item) => item.id === asset.activeGroupId) ?? null;
  const changeProfile = (patch: Partial<CharacterProfile>) => {
    if (asset.profile) onChange({ profile: { ...asset.profile, ...patch } });
  };

  return (
    <section className="assetView" aria-label="资产详情">
      <header className="assetViewHeader"><h2>{asset.name}</h2><span>{asset.kind === 'character' ? '角色' : asset.kind === 'scene' ? '场景' : '道具'}</span></header>
      <div className="assetViewBody">
        <div className="assetDetails">
          <h3>{asset.kind === 'character' ? '角色画像' : '资产信息'}</h3>
          <label>{asset.kind === 'character' ? '姓名' : '名称'}<Input aria-label={asset.kind === 'character' ? '姓名' : '名称'}
            value={asset.name} onChange={(event) => onChange({ name: event.target.value })} /></label>
          {asset.profile && <>
            <div className="assetFieldRow">
              <label>定位<Select aria-label="定位" value={asset.profile.role} onChange={(role) => changeProfile({ role })}
                options={['', '主角', '配角', '群演'].map((value) => ({ value, label: value || '未设置' }))} /></label>
              <label>性别<Input aria-label="性别" value={asset.profile.gender} onChange={(event) => changeProfile({ gender: event.target.value })} /></label>
              <label>年龄<Input aria-label="年龄" value={asset.profile.age} onChange={(event) => changeProfile({ age: event.target.value })} /></label>
            </div>
            <label>身份<Input aria-label="身份" value={asset.profile.identity} onChange={(event) => changeProfile({ identity: event.target.value })} /></label>
            <label>外貌<Input.TextArea aria-label="外貌" autoSize={{ minRows: 2 }} value={asset.profile.appearance}
              onChange={(event) => changeProfile({ appearance: event.target.value })} /></label>
            <label>性格<Input.TextArea aria-label="性格" autoSize={{ minRows: 2 }} value={asset.profile.personality}
              onChange={(event) => changeProfile({ personality: event.target.value })} /></label>
          </>}
          <label>描述<Input.TextArea aria-label="资产描述" autoSize={{ minRows: 2 }} value={asset.description}
            onChange={(event) => onChange({ description: event.target.value })} /></label>
          <label>标签<Select mode="tags" aria-label="资产标签" value={asset.tags} onChange={(tags) => onChange({ tags })}
            placeholder="添加标签" options={[]} /></label>
        </div>
        <div className="assetMedia">
          <div className="assetMediaHeading"><h3>图片</h3>
            {asset.imageGroups.length > 1 && <Select aria-label="图片组" value={asset.activeGroupId}
              onChange={onSelectGroup} options={asset.imageGroups.map((item, index) => ({
                value: item.id, label: index === 0 ? '最新生成' : '历史图片 ' + index,
              }))} />}
          </div>
          <ImageGroupGallery group={group} adoptedImage={asset.adoptedImage} previewMediaId={previewMediaId}
            onPreview={onPreview} onAdopt={onAdopt} />
          <div className="assetReferences">
            <span>参考图片</span>
            {references.map((reference) => <div className="assetReference" key={reference.media_id}>
              {reference.url && <img src={reference.url} alt="" />}
              <span>{reference.name}</span>
              <button type="button" aria-label={'移除图片参考：' + reference.name}
                onClick={() => onReferencesChange(references.filter((item) => item.media_id !== reference.media_id))}><X size={12} /></button>
            </div>)}
            <Popover trigger="click" title="添加参考图片" content={<div className="assetReferenceMenu">
              {availableReferences.filter((item) => !references.some((reference) => reference.media_id === item.media_id))
                .map((item) => <button type="button" key={item.media_id} onClick={() => onReferencesChange([...references, item])}>
                  {item.url && <img src={item.url} alt="" />}{item.name}</button>)}
            </div>}>
              <Button size="small" type="text" aria-label="添加参考图片" icon={<Plus size={15} />} />
            </Popover>
          </div>
          <GenerationComposer prompt={asset.imagePrompt} onPrompt={(imagePrompt) => onChange({ imagePrompt })}
            model={<span>演示模型</span>} sendDisabled={false} busy={false} onSend={onGenerate}
            promptInput={(id) => <AssetPromptInput id={id} ariaLabel="资产图片提示词" value={asset.imagePrompt}
              references={references} onChange={(imagePrompt) => onChange({ imagePrompt })} />} />
        </div>
      </div>
    </section>
  );
}