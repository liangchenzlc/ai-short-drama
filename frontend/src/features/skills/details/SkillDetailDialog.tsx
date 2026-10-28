import { errorMessage } from '@host/api/http';
import { skillsApi } from '@host/api/skills/catalog';
import { modeChoice, modeLabel, modeOptions, modeValues } from '@host/features/skills/library/skill-model';
import { SkillFileBrowser } from '@host/features/skills/resources/SkillFileBrowser';
import { Dialog } from '@host/shared/ui/Dialog';
import type { AgentSkill, AgentSkillDetail } from '@host/types/assistant/engine';
import type { SkillModeChoice } from '@host/types/skills/library/skill-model';
import { Button, Dropdown, Segmented, Skeleton, Switch, Typography } from 'antd';
import { Ellipsis } from 'lucide-react';
import { useEffect, useState } from 'react';
export function SkillDetailDialog({
  accountId,
  item,
  disabled,
  onClose,
  onLoaded,
  onUpdate,
  onExport,
  onDelete,
  onEnabled,
  onMode,
}: {
  accountId: string;
  item: AgentSkill;
  disabled: boolean;
  onClose: () => void;
  onLoaded: (detail: AgentSkillDetail) => void;
  onUpdate: (detail: AgentSkillDetail) => void;
  onExport: (item: AgentSkill) => void;
  onDelete: (item: AgentSkill) => void;
  onEnabled: (item: AgentSkill, enabled: boolean) => void;
  onMode: (item: AgentSkill, modes: AgentSkill['modes']) => void;
}) {
  const [detail, setDetail] = useState<AgentSkillDetail | null>(null);
  const [issue, setIssue] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setIssue('');
    setDetail(null);
    skillsApi
      .skill(item.id, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setDetail(value);
        onLoaded(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setIssue(errorMessage(cause));
      });
    return () => controller.abort();
  }, [item.id, item.content_version, revision, onLoaded]);
  const current = detail
    ? {
        ...detail,
        enabled: item.enabled,
        settings_version: item.settings_version,
      }
    : item;
  return (
    <Dialog title={current.name} className="skill-preview-dialog skill-detail-dialog" onClose={onClose}>
      <div className="skill-preview-summary">
        <Typography.Paragraph
          className="skill-preview-purpose"
          ellipsis={{
            rows: 2,
            expandable: 'collapsible',
            symbol: (expanded) => (expanded ? '收起用途' : '展开用途'),
          }}
        >
          {current.description}
        </Typography.Paragraph>
        <div className="skill-detail-toolbar">
          <div className="skill-mode-control">
            <span>适用模式</span>
            {item.builtin ? (
              <span>{modeLabel(current.modes)}</span>
            ) : (
              <Segmented
                aria-label="适用模式"
                value={modeChoice(current.modes)}
                options={modeOptions}
                disabled={disabled || !detail}
                onChange={(value) => onMode(current, modeValues(value as SkillModeChoice))}
              />
            )}
          </div>
          <div className="skill-detail-actions">
            <Switch
              aria-label={`启用 ${current.name}`}
              checked={current.enabled}
              disabled={disabled || !detail || (!current.enabled && !current.compatible)}
              onChange={(value) => onEnabled(current, value)}
            />
            {item.builtin ? (
              <Button disabled={disabled || !detail} onClick={() => onExport(current)}>
                导出 ZIP
              </Button>
            ) : (
              <>
                <Button
                  type="primary"
                  disabled={disabled || !detail}
                  onClick={() =>
                    detail &&
                    onUpdate({
                      ...detail,
                      enabled: current.enabled,
                      settings_version: current.settings_version,
                    })
                  }
                >
                  更新文件
                </Button>
                <Dropdown
                  trigger={['click']}
                  menu={{
                    items: [
                      { key: 'export', label: '导出 ZIP' },
                      { key: 'delete', label: '删除', danger: true },
                    ],
                    onClick: ({ key }) => (key === 'export' ? onExport(current) : onDelete(current)),
                  }}
                >
                  <Button aria-label="更多 Skill 操作" icon={<Ellipsis size={16} />} disabled={disabled || !detail} />
                </Dropdown>
              </>
            )}
          </div>
        </div>
        {!current.compatible && (
          <p role="status" className="skill-inline-issue">
            {current.issues[0]}
          </p>
        )}
      </div>
      {issue ? (
        <div className="skill-file-state" role="alert">
          <p>读取失败：{issue}</p>
          <Button onClick={() => setRevision((value) => value + 1)}>重试</Button>
        </div>
      ) : !detail ? (
        <div className="skill-file-state" role="status" aria-label="正在读取 Skill">
          <Skeleton active />
        </div>
      ) : (
        <SkillFileBrowser
          identity={`${accountId}:${detail.id}:${detail.content_version}`}
          skillId={detail.id}
          instructions={detail.instructions}
          resources={detail.resources.map((resource) => ({
            ...resource,
            content: null,
            origin_version: detail.content_version,
          }))}
        />
      )}
    </Dialog>
  );
}
