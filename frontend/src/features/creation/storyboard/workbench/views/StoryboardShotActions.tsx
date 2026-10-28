import type { StoryboardShotActionsProps } from '@host/types/creation/storyboard/workbench/views';
import { Button, Dropdown } from 'antd';
import { Ellipsis } from 'lucide-react';

/** 主编辑区和历史列表共用菜单视图，排序事务仍由工作台串行执行。 */
export function StoryboardShotActions({
  readOnly,
  selectedShot,
  batchEnabled,
  batchMode,
  total,
  open,
  selectionDisabled,
  triggerRef,
  onOpenChange,
  onAction,
}: StoryboardShotActionsProps) {
  return !readOnly && (selectedShot || batchEnabled) ? (
    <Dropdown
      trigger={['click']}
      overlayClassName="standard-creation-popup"
      open={open}
      onOpenChange={onOpenChange}
      menu={{
        items: [
          ...(batchEnabled
            ? [
              {
                key: 'batch',
                label: batchMode ? '退出批量操作' : '批量操作',
              },
              { type: 'divider' as const },
            ]
            : []),
          ...(selectedShot
            ? [
              {
                key: 'up',
                label: '上移',
                disabled: selectedShot.position <= 1,
              },
              {
                key: 'down',
                label: '下移',
                disabled: total === null || selectedShot.position >= total,
              },
            ]
            : []),
        ],
        onClick: ({ key }) => onAction(key as 'up' | 'down' | 'batch'),
        onKeyDown: (event) => {
          if (event.key !== 'Escape') return;
          event.preventDefault();
          event.stopPropagation();
          onOpenChange(false);
          requestAnimationFrame(() => triggerRef.current?.focus());
        },
      }}
    >
      <Button
        type="text"
        aria-label={
          selectedShot ? `分镜 ${String(selectedShot.position).padStart(2, '0')} 更多操作` : '分镜列表更多操作'
        }
        title="更多操作"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={selectionDisabled}
        icon={<Ellipsis size={18} />}
        onClick={(event) => {
          if (event.currentTarget instanceof HTMLButtonElement) triggerRef.current = event.currentTarget;
        }}
      />
    </Dropdown>
  ) : null;
}
