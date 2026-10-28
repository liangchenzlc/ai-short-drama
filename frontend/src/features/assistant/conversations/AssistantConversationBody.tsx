import { assistantApi } from '@host/api/assistant/conversations/requests';
import { AgentContextComposer } from '@host/features/assistant/attachments/AgentContextComposer';
import { AssistantComposerTools } from '@host/features/assistant/composer/AssistantComposerTools';
import { AssistantMentionTextarea } from '@host/features/assistant/composer/AssistantMentionTextarea';
import { useAssistantComposer } from '@host/features/assistant/composer/useAssistantComposer';
import { AssistantDiscussionHistory } from '@host/features/assistant/history/AssistantDiscussionHistory';
import { AssistantTranscript } from '@host/features/assistant/messages/AssistantTranscript';
import { AssistantBatchUndo } from '@host/features/assistant/operations/AssistantBatchUndo';
import { materialIdentity } from '@host/features/assistant/references/assistant-materials';
import { skillToken } from '@host/features/assistant/skills/assistant-skill-token';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { AssistantComposerToolsController } from '@host/types/assistant/composer/tools';
import type { AssistantConversationProps } from '@host/types/assistant/conversations/AssistantConversationBody';
import { Button, Select, Tooltip } from 'antd';
import { LockKeyhole } from 'lucide-react';
import { useRef } from 'react';
export function AssistantConversationBody(props: AssistantConversationProps) {
  const {
    conversation,
    accountId,
    projectId,
    executionTarget,
    layout,
    readOnly,
    mentionReferences,
    onOpenModelSettings,
  } = props;
  const {
    runtime,
    pinned,
    historyRequest,
    setHistoryRequest,
    historyBusy,
    setHistoryBusy,
    historyPending,
    setHistoryPending,
    undoRequest,
    setUndoRequest,
    undoBusy,
    setUndoBusy,
    undoPending,
    setUndoPending,
    restored,
    draft,
    setDraft,
    skills,
    setSkills,
    setSkillsDirty,
    draftError,
    setDraftError,
    models,
    modelId,
    setModelId,
    modelsLoading,
    modelsError,
    setModelRevision,
    contextBusy,
    setContextBusy,
    contextBlockReason,
    setContextBlockReason,
    materials,
    setMaterials,
    restoreMaterials,
    setRestoreMaterials,
    restoringMaterials,
    materialsError,
    setMaterialsError,
    setMaterialsRevision,
    task,
    selectedSkills,
    composerVersion,
    pendingControl,
    busy,
    actionError,
    setActionError,
    uncertain,
    inputContext,
    active,
    send,
    control,
    interject,
    stop,
    addMaterials,
    newTask,
    changeDraft,
    changeSkills,
    contextDisabled,
    stopTaskId,
    stopping,
    blocked,
    taskModelName,
  } = useAssistantComposer(props);
  const modelControl = task ? (
    <Tooltip title="当前任务沿用此模型，任务结束后可切换。" trigger={['hover', 'focus', 'click']}>
      <Button
        type="text"
        size="small"
        className="ai-assistant-model ai-assistant-model-locked"
        aria-label={`助手模型：${taskModelName}，当前任务已锁定`}
        icon={<LockKeyhole size={12} aria-hidden="true" />}
      >
        <span className="ai-assistant-model-name">{taskModelName}</span>
      </Button>
    </Tooltip>
  ) : (
    <Select
      className="ai-assistant-model"
      classNames={
        layout === 'standard'
          ? {
              popup: {
                root: 'standard-assistant-select-popup standard-creation-popup',
              },
            }
          : undefined
      }
      variant="borderless"
      size="small"
      aria-label="助手模型"
      value={modelId}
      loading={modelsLoading}
      disabled={runtime.loading || !!busy || contextBusy || !!uncertain || readOnly || runtime.accessEnded}
      placeholder="选择模型"
      options={models.map((item) => ({ value: item.id, label: item.name }))}
      onChange={setModelId}
    />
  );
  const toolsDisabled = contextDisabled || contextBusy || runtime.loading || runtime.accessEnded;
  const toolsRef = useRef<AssistantComposerToolsController>(null);
  const toolControl = (
    <AssistantComposerTools
      ref={toolsRef}
      layout={layout}
      task={task}
      executionTarget={executionTarget}
      toolsDisabled={toolsDisabled}
      contextBlockReason={contextBlockReason}
      inputContext={inputContext}
      mentionReferences={mentionReferences}
      addMaterials={addMaterials}
      setActionError={setActionError}
      selectedSkills={selectedSkills}
      changeSkills={changeSkills}
    />
  );
  return (
    <div className="ai-assistant-runtime">
      {runtime.error ? (
        layout === 'standard' ? (
          <StudioFeedback
            scope="chat"
            type="error"
            title="AI 创作助手"
            message={runtime.error}
            noticeKey={`assistant-connection:${conversation.id}`}
            actions={
              <Button size="small" onClick={() => runtime.reconnect()}>
                重新连接
              </Button>
            }
          />
        ) : (
          <div className="ai-assistant-notice" role="alert">
            <span>{runtime.error}</span>
            <Button
              size="small"
              onClick={() => {
                runtime.reconnect();
              }}
            >
              重新连接
            </Button>
          </div>
        )
      ) : null}
      {actionError || draftError ? (
        layout === 'standard' ? (
          <StudioFeedback
            scope="chat"
            type="error"
            title="AI 创作助手"
            message={actionError || draftError}
            noticeKey={`assistant-error:${conversation.id}`}
            actions={
              <Button
                size="small"
                onClick={() => {
                  setActionError('');
                  setDraftError('');
                }}
              >
                知道了
              </Button>
            }
          />
        ) : (
          <div className="ai-assistant-notice" role="alert">
            <span>{actionError || draftError}</span>
            <Button
              size="small"
              onClick={() => {
                setActionError('');
                setDraftError('');
              }}
            >
              知道了
            </Button>
          </div>
        )
      ) : null}
      {modelsError || (!modelsLoading && !models.length) ? (
        layout === 'standard' ? (
          <StudioFeedback
            scope="chat"
            type={modelsError ? 'error' : 'warning'}
            quiet={!modelsError}
            persistent
            title="助手模型"
            message={modelsError || '请先添加并启用文本模型。'}
            noticeKey={`assistant-model:${conversation.id}`}
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
        ) : (
          <div className="ai-assistant-notice" role="status">
            <span>{modelsError || '请先添加并启用文本模型。'}</span>
            <Button size="small" onClick={onOpenModelSettings}>
              模型配置
            </Button>
            <Button size="small" onClick={() => setModelRevision((value) => value + 1)}>
              重新读取
            </Button>
          </div>
        )
      ) : null}
      <AssistantTranscript
        pinned={pinned}
        runtime={runtime}
        layout={layout}
        active={active}
        stopping={stopping}
        uncertain={!!uncertain}
        contextDisabled={contextDisabled}
        readOnly={readOnly}
        busy={!!busy}
        setDraft={setDraft}
        setUndoRequest={setUndoRequest}
        setHistoryRequest={setHistoryRequest}
        stop={stop}
      />
      <footer className="ai-assistant-composer">
        {restoringMaterials && (
          <p className="ai-assistant-meta" role="status">
            正在核对草稿引用…
          </p>
        )}
        {materialsError && (
          <StudioFeedback
            scope="chat"
            type="error"
            persistent
            title="引用恢复"
            message={materialsError}
            actions={
              <>
                {!!restoreMaterials.length && (
                  <Button
                    size="small"
                    disabled={restoringMaterials}
                    onClick={() => setMaterialsRevision((value) => value + 1)}
                  >
                    重试恢复引用
                  </Button>
                )}
                <Button
                  size="small"
                  disabled={restoringMaterials}
                  onClick={() => {
                    setRestoreMaterials([]);
                    setMaterials([]);
                    setMaterialsError('');
                  }}
                >
                  移除恢复引用
                </Button>
              </>
            }
          />
        )}
        {task && (
          <div className="ai-assistant-task-context">
            <span title={task.goal}>当前任务：{task.goal}</span>
            <Button size="small" type="text" disabled={contextDisabled || !!stopTaskId} onClick={newTask}>
              开始新任务
            </Button>
          </div>
        )}
        {task && task.input_revision !== task.applied_input_revision && (
          <p className="ai-assistant-meta" role="status">
            补充要求已排队，将在当前步骤结束后生效。
          </p>
        )}
        {contextBlockReason ? (
          <p className="ai-assistant-meta" role="status">
            {contextBlockReason}
          </p>
        ) : null}
        <AgentContextComposer
          key={composerVersion}
          ref={inputContext}
          conversation={{ id: conversation.id, project_id: projectId }}
          layout={layout}
          attachmentApi={assistantApi}
          dialogClassName={`ai-assistant-context-dialog${layout === 'standard' ? ' standard-creation-dialog standard-assistant-dialog' : ''}`}
          materials={materials.map((item) => ({
            key: materialIdentity(item),
            name: item.name,
            kind: item.kind,
            thumbnail: null,
            onRemove: () => setMaterials((previous) => previous.filter((candidate) => candidate !== item)),
          }))}
          initialSkills={composerVersion ? [] : restored.skills}
          onSkills={setSkills}
          onSkillsEdited={() => setSkillsDirty(true)}
          presentation="assistant"
          skillsManaged
          disabled={readOnly || runtime.accessEnded || !!busy || !!uncertain}
          onBusy={setContextBusy}
          onBlockReason={setContextBlockReason}
          input={
            <div className="ai-assistant-input">
              <AssistantMentionTextarea
                layout={layout}
                projectId={projectId}
                references={mentionReferences}
                highlights={[...skills.map(skillToken), ...materials.map((item) => `@${item.name}`)]}
                value={draft}
                disabled={readOnly || runtime.accessEnded}
                modelControl={modelControl}
                toolControl={toolControl}
                onChange={changeDraft}
                onSubmit={() => {
                  if (!blocked) void send();
                }}
                action={{
                  generating: !!stopTaskId,
                  stopping,
                  submitting: busy === 'send' || busy === 'save',
                  disabled: stopTaskId
                    ? contextDisabled || runtime.loading || runtime.accessEnded || stopping
                    : blocked,
                  canStop: task?.interruptions?.stop ?? true,
                  canInterject: !!stopTaskId && !blocked,
                  interjecting: busy === 'send' || busy === 'save',
                  onInterject: () => {
                    void interject();
                  },
                  onStop: () => {
                    if (stopTaskId) void stop(stopTaskId);
                  },
                }}
                skillMode={task?.execution_target?.mode ?? executionTarget?.mode}
                onBrowseSkills={() => {
                  toolsRef.current?.openSkills();
                }}
                onSkill={(skill) => {
                  if (contextDisabled || runtime.loading || runtime.accessEnded) return false;
                  if (selectedSkills.length >= 8 && !selectedSkills.some((item) => item.id === skill.id)) {
                    setActionError('一个任务最多手动指定 8 项方法，请先移除其他方法。');
                    return false;
                  }
                  setSkills([
                    ...selectedSkills.filter((item) => item.id !== skill.id),
                    {
                      id: skill.id,
                      name: skill.name,
                      content_version: skill.content_version,
                    },
                  ]);
                  setSkillsDirty(true);
                  return true;
                }}
                onReference={async (reference) => {
                  if (uncertain || busy) {
                    setActionError('上一条消息的结果还没确认，确认后再添加新素材。');
                    return false;
                  }
                  return addMaterials([reference]);
                }}
              />
            </div>
          }
          modelControl={null}
          sendControl={null}
        />
        {readOnly ? (
          <span className={layout === 'standard' ? 'sr-only' : 'ai-assistant-meta'}>
            当前项目只读，可以查看已有对话。
          </span>
        ) : null}
        {pendingControl && !busy && (
          <StudioFeedback
            scope="chat"
            type="warning"
            persistent
            title="操作结果待确认"
            message="这次操作可能已生效，结果暂时无法确认。为避免重复执行，请先刷新状态。"
            noticeKey={`assistant-control:${pendingControl.targetId}`}
            actions={
              <Button
                size="small"
                disabled={readOnly || runtime.accessEnded}
                onClick={() => void control(pendingControl.targetId, pendingControl.body, pendingControl)}
              >
                重发这次操作
              </Button>
            }
          />
        )}
        {busy === 'save' ? (
          <span className={layout === 'standard' ? 'sr-only' : 'ai-assistant-meta'} role="status">
            正在保存…
          </span>
        ) : null}
        {uncertain ? (
          layout === 'standard' ? (
            <StudioFeedback
              scope="chat"
              type="warning"
              persistent
              title="发送结果待确认"
              message="消息可能已提交，但结果暂时无法确认。为避免重复生成和计费，请先刷新状态。"
              noticeKey={`assistant-pending:${conversation.id}`}
              actions={
                <div className="ai-assistant-uncertain">
                  <Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>
                    刷新状态
                  </Button>
                  <Button
                    size="small"
                    disabled={readOnly || runtime.accessEnded || !!busy || contextBusy}
                    onClick={() => void send(uncertain)}
                  >
                    重发原消息
                  </Button>
                </div>
              }
            />
          ) : (
            <div className="ai-assistant-uncertain" role="status">
              <p>消息可能已提交，但结果暂时无法确认。为避免重复生成和计费，请先刷新状态。</p>
              <Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>
                刷新状态
              </Button>
              <Button
                size="small"
                disabled={readOnly || runtime.accessEnded || !!busy || contextBusy}
                onClick={() => void send(uncertain)}
              >
                重发原消息
              </Button>
            </div>
          )
        ) : null}
      </footer>
      {layout === 'standard' && (
        <AssistantDiscussionHistory
          accountId={accountId}
          conversationId={conversation.id}
          request={historyRequest}
          disabled={
            readOnly || !!busy || undoBusy || (undoPending && !historyPending) || !!uncertain || !!pendingControl
          }
          accessEnded={runtime.accessEnded}
          onClose={() => setHistoryRequest(null)}
          onAccepted={runtime.accept}
          onBlocked={setHistoryPending}
          onBusy={setHistoryBusy}
          onCorrect={(content) => {
            newTask();
            setDraft(`纠正此前讨论：${content}`);
          }}
        />
      )}
      {layout === 'standard' && (
        <AssistantBatchUndo
          accountId={accountId}
          conversationId={conversation.id}
          operationId={undoRequest}
          disabled={
            readOnly || !!busy || historyBusy || (historyPending && !undoPending) || !!uncertain || !!pendingControl
          }
          accessEnded={runtime.accessEnded}
          onClose={() => setUndoRequest(null)}
          onBusy={setUndoBusy}
          onBlocked={setUndoPending}
          onUpdated={() => void runtime.reload()}
        />
      )}
    </div>
  );
}
