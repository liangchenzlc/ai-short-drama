import { AssetPromptInput } from '@host/features/creation/assets/images/AssetPromptInput';
import { GenerationComposer } from '@host/features/creation/shared/generation/GenerationComposer';
import { confirmAction } from '@host/shared/ui/confirm';
import { ImageGroupGallery } from '@host/features/media/image-groups/imageGroupGallery';
import type { AssetPromptReference } from '@host/types/creation/assets/images/asset-prompt-references';
import type { AssetView as AssetViewData } from '@host/types/creation/workspace/workbenchView';
import { Button, Input, Popover, Select } from 'antd';
import { Plus, X } from 'lucide-react';
import './assetView.css';

type AssetPatch = Partial<Pick<AssetViewData, 'kind' | 'name' | 'description' | 'profile' | 'imagePrompt'>>;
type AssetDetailsValue = Omit<Pick<AssetViewData, 'kind' | 'name' | 'description' | 'profile'>, 'kind'> & {
  kind: AssetViewData['kind'] | null;
};
type AssetDetailsPatch = Partial<Omit<AssetDetailsValue, 'kind'>> & { kind?: AssetViewData['kind'] };

const sceneFields: { key: string; label: string; rows?: number }[] = [
  { key: 'scene_type', label: '场景类型' }, { key: 'location', label: '地点' },
  { key: 'time', label: '时间' }, { key: 'lighting', label: '光线' },
  { key: 'environment', label: '环境布置', rows: 2 }, { key: 'atmosphere', label: '氛围', rows: 2 },
];
const propFields: { key: string; label: string; rows?: number }[] = [
  { key: 'prop_type', label: '道具类型' }, { key: 'material', label: '材质' },
  { key: 'color', label: '颜色' }, { key: 'size', label: '尺寸' },
  { key: 'condition', label: '状态' }, { key: 'appearance', label: '外观特征', rows: 2 },
  { key: 'purpose', label: '用途', rows: 2 },
];

export function AssetView({ asset, previewMediaId, references, availableReferences, onChange, onPreview, onAdopt,
  onSelectGroup, onReferencesChange, onGenerate, onRetryItem }: {
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
  onRetryItem?: (itemId: string) => void;
}) {
  const group = asset.imageGroups.find((item) => item.id === asset.activeGroupId) ?? null;
  return (
    <section className="assetView" aria-label="资产详情">
      <header className="assetViewHeader"><h2>{asset.name}</h2><span>{asset.kind === 'character' ? '角色' : asset.kind === 'scene' ? '场景' : '道具'}</span></header>
      <div className="assetViewBody">
        <AssetDetails asset={asset} onChange={onChange} />
        <div className="assetMedia">
          {asset.imageGroups.length > 1 && <div className="assetMediaHeading"><Select aria-label="图片组" value={asset.activeGroupId}
              onChange={onSelectGroup} options={asset.imageGroups.map((item, index) => ({
                value: item.id, label: index === 0 ? '最新生成' : '历史图片 ' + index,
              }))} /></div>}
          <ImageGroupGallery group={group} adoptedImage={asset.adoptedImage} previewMediaId={previewMediaId}
            onPreview={onPreview} onAdopt={onAdopt} onRetryItem={onRetryItem} />
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

export function AssetDetails({ asset, onChange, disabled = false, nameAriaLabel, descriptionAriaLabel }: {
  asset: AssetDetailsValue;
  onChange: (patch: AssetDetailsPatch) => void;
  disabled?: boolean;
  nameAriaLabel?: string;
  descriptionAriaLabel?: string;
}) {
  const changeProfile = (key: string, value: string) => onChange({ profile: { ...asset.profile, [key]: value } });
  async function changeKind(kind: AssetViewData['kind']) {
    if (kind !== asset.kind && Object.values(asset.profile).some(Boolean) &&
      !(await confirmAction('更改素材类型将清除当前资料，无法撤销。', {
        title: '更改素材类型', confirmText: '确认更改', danger: true,
      }))) return;
    onChange({ kind, profile: {} });
  }
  return (
    <div className="assetDetails">
      <label>素材类型<Select aria-label="素材类型" value={asset.kind ?? undefined} placeholder="选择类型" disabled={disabled} onChange={(kind: AssetViewData['kind']) => void changeKind(kind)}
        options={[{ value: 'character', label: '角色' }, { value: 'scene', label: '场景' }, { value: 'prop', label: '道具' }]} /></label>
      <label>{asset.kind === 'character' ? '姓名' : '名称'}<Input aria-label={nameAriaLabel ?? (asset.kind === 'character' ? '姓名' : '名称')}
        value={asset.name} maxLength={255} disabled={disabled} onChange={(event) => onChange({ name: event.target.value })} /></label>
      {asset.kind === 'character' && <>
        <div className="assetFieldRow">
          <label>定位<Select aria-label="定位" value={asset.profile.role ?? ''} disabled={disabled} onChange={(role) => changeProfile('role', role)}
            options={['', '主角', '配角', '群演'].map((value) => ({ value, label: value || '未设置' }))} /></label>
          <label>性别<Input aria-label="性别" value={asset.profile.gender ?? ''} maxLength={4000} disabled={disabled} onChange={(event) => changeProfile('gender', event.target.value)} /></label>
          <label>年龄<Input aria-label="年龄" value={asset.profile.age ?? ''} maxLength={4000} disabled={disabled} onChange={(event) => changeProfile('age', event.target.value)} /></label>
        </div>
        <label>身份<Input aria-label="身份" value={asset.profile.identity ?? ''} maxLength={4000} disabled={disabled} onChange={(event) => changeProfile('identity', event.target.value)} /></label>
        <label>外貌<Input.TextArea aria-label="外貌" autoSize={{ minRows: 2 }} value={asset.profile.appearance ?? ''} maxLength={4000} disabled={disabled}
          onChange={(event) => changeProfile('appearance', event.target.value)} /></label>
        <label>性格<Input.TextArea aria-label="性格" autoSize={{ minRows: 2 }} value={asset.profile.personality ?? ''} maxLength={4000} disabled={disabled}
          onChange={(event) => changeProfile('personality', event.target.value)} /></label>
      </>}
      {(asset.kind === 'scene' || asset.kind === 'prop') && <div className="assetExtraFields">{(asset.kind === 'scene' ? sceneFields : propFields).map((field) =>
        <label key={field.key} className={field.rows ? 'isWide' : undefined}>{field.label}{field.rows ?
          <Input.TextArea aria-label={field.label} autoSize={{ minRows: field.rows }} value={asset.profile[field.key] ?? ''} maxLength={4000} disabled={disabled}
            onChange={(event) => changeProfile(field.key, event.target.value)} /> :
          <Input aria-label={field.label} value={asset.profile[field.key] ?? ''} maxLength={field.key === 'time' ? 60 : 4000} disabled={disabled}
            onChange={(event) => changeProfile(field.key, event.target.value)} />}</label>)}</div>}
      <label>描述<Input.TextArea aria-label={descriptionAriaLabel ?? '资产描述'} autoSize={{ minRows: 2 }} value={asset.description} maxLength={8000} disabled={disabled}
        onChange={(event) => onChange({ description: event.target.value })} /></label>
    </div>
  );
}
