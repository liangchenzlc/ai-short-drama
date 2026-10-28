import { CreationModelSelect } from '@host/features/ai-config/selection/CreationModelSelect';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { AssetKind } from '@host/types/creation/assets/management';
import { Button, Checkbox, Input } from 'antd';

import { allKinds, labels } from '@host/features/creation/assets/extraction/presentation';
import type { ExtractionSettingsProps } from '@host/types/creation/assets/extraction/views';
export function ExtractionSettings({
  projectId,
  readOnly,
  loaded,
  busy,
  canExtract,
  kinds,
  setKinds,
  instructions,
  setInstructions,
  modelId,
  onModelChange,
  pending,
  onHistory,
  onConfirmScript,
}: ExtractionSettingsProps) {
  return (
    <div className="extraction-settings">
      <p>从本集当前剧本整理文字素材，核对后加入本集素材库。</p>
      {!canExtract && (
        <StudioFeedback
          type="info"
          quiet
          persistent={!readOnly && loaded}
          title="素材提取来源"
          noticeKey={`asset-extraction-source:${projectId}`}
          message={
            readOnly
              ? '当前为只读模式，可查看已有提取结果。'
              : !loaded
                ? '正在等待剧本载入。'
                : '先编写或采用一份剧本，再提取素材。'
          }
          actions={
            !readOnly && loaded ? (
              <Button
                onClick={() => {
                  onConfirmScript();
                }}
              >
                去编写剧本
              </Button>
            ) : undefined
          }
        />
      )}
      <fieldset disabled={busy || readOnly}>
        <legend>提取范围</legend>
        <Checkbox.Group
          aria-label="提取范围"
          value={kinds}
          options={allKinds.map((value) => ({
            value,
            label: labels[value],
          }))}
          onChange={(values) => setKinds(values as AssetKind[])}
        />
      </fieldset>
      <label className="writing-control">
        <span>
          补充要求 <small>选填</small>
        </span>
        <Input.TextArea
          aria-label="提取补充要求"
          rows={4}
          maxLength={4000}
          showCount
          value={instructions}
          disabled={busy || readOnly}
          onChange={(event) => setInstructions(event.target.value)}
          placeholder="例如：仅提取有台词的角色，保留推动剧情的重要道具。"
        />
      </label>
      <div className="writing-control">
        <span>文本模型</span>
        <CreationModelSelect
          presentation="standard-creation"
          kind="text"
          label="素材提取文本模型"
          value={modelId}
          disabled={busy || readOnly}
          onChange={onModelChange}
        />
      </div>
      <p className="extraction-hint">提取名称、描述与图片生成提示词，此处不生成图片。</p>
      {pending && (
        <StudioFeedback
          type="info"
          quiet
          persistent
          title="素材提取任务"
          noticeKey={`asset-extraction-pending:${projectId}`}
          message="已有提取任务处理中，可以从提取记录查看进度。"
          actions={<Button onClick={() => onHistory()}>查看提取记录</Button>}
        />
      )}
    </div>
  );
}
