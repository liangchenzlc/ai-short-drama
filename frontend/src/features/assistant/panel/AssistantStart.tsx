import { AssistantMentionTextarea } from '@host/features/assistant/composer/AssistantMentionTextarea';
import { useAssistantModels } from '@host/features/assistant/composer/useAssistantModels';
import { AssistantWelcome } from '@host/features/assistant/messages/AssistantWelcome';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import { Button, Select } from 'antd';
import { useState } from 'react';
/** 尚未建立会话时的首屏：直接可输入，发送后才创建会话。 */
export function AssistantStart({
  projectId,
  readOnly,
  submitting,
  onOpenModelSettings,
  onStart,
}: {
  projectId: string;
  readOnly: boolean;
  submitting: boolean;
  onOpenModelSettings: () => void;
  onStart: (text: string, modelId: string) => void;
}) {
  const [draft, setDraft] = useState('');
  const { models, modelId, setModelId, modelsLoading, modelsError, setModelRevision } = useAssistantModels();
  const ready = !!modelId && !modelsLoading;
  function submit() {
    const text = draft.trim();
    if (!text || !modelId || readOnly || submitting) return;
    onStart(text, modelId);
  }
  const openSettings = modelsError || (!modelsLoading && !models.length);
  return (
    <div className="ai-assistant-runtime">
      <div className="ai-assistant-log">
        <AssistantWelcome readOnly={readOnly} onPick={setDraft} />
      </div>
      <footer className="ai-assistant-composer">
        {openSettings ? (
          <StudioFeedback
            scope="chat"
            type={modelsError ? 'error' : 'warning'}
            quiet={!modelsError}
            persistent
            title="助手模型"
            message={modelsError || '请先添加并启用文本模型。'}
            noticeKey="assistant-model:new"
            actions={
              <>
                <Button size="small" onClick={onOpenModelSettings}>
                  模型配置
                </Button>
                <Button size="small" onClick={() => setModelRevision((value) => value + 1)}>
                  重新读取
                </Button>
              </>
            }
          />
        ) : null}
        <div className="ai-assistant-input">
          <AssistantMentionTextarea
            layout="standard"
            projectId={projectId}
            value={draft}
            disabled={readOnly}
            onChange={setDraft}
            onSubmit={submit}
            onReference={async () => true}
            modelControl={
              <Select
                className="ai-assistant-model"
                classNames={{
                  popup: { root: 'standard-assistant-select-popup standard-creation-popup' },
                }}
                variant="borderless"
                size="small"
                aria-label="助手模型"
                value={modelId}
                loading={modelsLoading}
                disabled={modelsLoading || readOnly}
                placeholder="选择模型"
                options={models.map((item) => ({ value: item.id, label: item.name }))}
                onChange={setModelId}
              />
            }
            action={{
              generating: false,
              stopping: false,
              submitting,
              disabled: readOnly || !ready || !draft.trim(),
              onStop: () => {},
            }}
          />
        </div>
      </footer>
    </div>
  );
}
