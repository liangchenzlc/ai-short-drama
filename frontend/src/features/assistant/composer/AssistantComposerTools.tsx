import { AssistantSkillPicker } from '@host/features/assistant/skills/AssistantSkillPicker';
import type { AssistantComposerToolsProps } from '@host/types/assistant/composer/tools';
import { Button, Dropdown, Popover } from 'antd';
import { Library, Plus } from 'lucide-react';
import { useImperativeHandle, useRef, useState } from 'react';
export function AssistantComposerTools({
  ref,
  layout,
  task,
  executionTarget,
  toolsDisabled,
  contextBlockReason,
  inputContext,
  mentionReferences,
  addMaterials,
  setActionError,
  selectedSkills,
  changeSkills,
}: AssistantComposerToolsProps) {
  const [addOpen, setAddOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const skillEntry = useRef<HTMLButtonElement>(null);
  const skillList = useRef<HTMLDivElement>(null);
  const canvasMode = executionTarget?.mode === 'infinite_canvas';
  useImperativeHandle(
    ref,
    () => ({
      openSkills() {
        setAddOpen(false);
        setSkillsOpen(true);
      },
    }),
    [],
  );

  function closeSkills() {
    setSkillsOpen(false);
    requestAnimationFrame(() => skillEntry.current?.focus());
  }
  return (
    <div className="ai-assistant-composer-tools">
      <Dropdown
        open={addOpen}
        onOpenChange={(open) => {
          setAddOpen(open);
          if (open) setSkillsOpen(false);
        }}
        trigger={['click']}
        placement="topLeft"
        overlayClassName={
          layout === 'standard' ? 'standard-assistant-menu standard-creation-menu standard-creation-popup' : undefined
        }
        disabled={toolsDisabled || !!contextBlockReason}
        menu={{
          items: [
            { key: 'image', label: '图片' },
            { key: 'text', label: '文本' },
            {
              key: 'canvas',
              label: (
                <span>
                  从当前画布选择
                  {!canvasMode && <small className="ai-assistant-menu-hint">仅画布模式可用</small>}
                </span>
              ),
              disabled: !canvasMode,
            },
            { key: 'assets', label: '从资产库选择' },
          ],
          onClick: ({ key }) => {
            setAddOpen(false);
            if (key === 'image' || key === 'text') inputContext.current?.openUpload(key);
            else if (key === 'assets') inputContext.current?.openAssets();
            else if (key === 'canvas') {
              if (mentionReferences?.length) void addMaterials(mentionReferences);
              else setActionError('请先在画布中选中需要引用的节点。');
            }
          },
        }}
      >
        <Button
          type="text"
          aria-label="添加附件"
          aria-expanded={addOpen}
          disabled={toolsDisabled || !!contextBlockReason}
          icon={<Plus size={17} />}
        />
      </Dropdown>
      <Popover
        open={skillsOpen}
        onOpenChange={(open) => {
          setSkillsOpen(open);
          if (open) setAddOpen(false);
        }}
        afterOpenChange={(open) => {
          if (open) skillList.current?.querySelector('input')?.focus();
        }}
        trigger="click"
        placement="top"
        destroyOnHidden
        classNames={{
          root: `assistant-skill-popover${layout === 'standard' ? ' standard-assistant-popover standard-creation-popup' : ''}`,
        }}
        content={
          <div
            ref={skillList}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                closeSkills();
              }
              if (event.key === 'Tab') {
                const controls = [
                  ...event.currentTarget.querySelectorAll<HTMLElement>(
                    'input:not([disabled]),button:not([disabled]),a[href]',
                  ),
                ].filter((node) => node.getClientRects().length > 0);
                if (
                  (event.shiftKey && document.activeElement === controls[0]) ||
                  (!event.shiftKey && document.activeElement === controls.at(-1))
                ) {
                  event.preventDefault();
                  closeSkills();
                }
              }
            }}
          >
            <AssistantSkillPicker
              selected={selectedSkills}
              activations={task?.skill_activations}
              mode={task?.execution_target?.mode ?? executionTarget?.mode}
              disabled={toolsDisabled}
              onChange={changeSkills}
            />
          </div>
        }
      >
        <Button
          ref={skillEntry}
          type="text"
          aria-label="选择 Skill"
          aria-expanded={skillsOpen}
          disabled={toolsDisabled}
          icon={<Library size={17} />}
          onKeyDown={(event) => {
            if (skillsOpen && event.key === 'Escape') {
              event.preventDefault();
              event.stopPropagation();
              closeSkills();
            }
          }}
        />
      </Popover>
    </div>
  );
}
