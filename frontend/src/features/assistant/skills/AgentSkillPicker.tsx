import { errorMessage } from '@host/api/http';
import { skillsApi } from '@host/api/skills/catalog';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { SkillFileBrowser } from '@host/features/skills/resources/SkillFileBrowser';
import { Dialog } from '@host/shared/ui/Dialog';
import type { AgentSkill, AgentSkillDetail } from '@host/types/assistant/engine';
import type { Props } from '@host/types/assistant/skills/AgentSkillPicker';
import { Button, Checkbox, Segmented, Skeleton } from 'antd';
import { useEffect, useState } from 'react';
import '../../skills/library/skills.css';
export function AgentSkillPicker(props: Props) {
  const auth = useAuth();
  const accountId = auth.user?.id ?? 'anonymous';
  return <SkillPicker key={accountId} {...props} accountId={accountId} />;
}
function SkillPicker({
  selected,
  disabled,
  onChange,
  onClose,
  dialogClassName,
  layout,
  accountId,
}: Props & { accountId: string }) {
  const [items, setItems] = useState<AgentSkill[]>([]);
  const [tab, setTab] = useState<'builtin' | 'personal'>('builtin');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentSkillDetail | null>(null);
  const [previewIssue, setPreviewIssue] = useState('');
  const [previewRevision, setPreviewRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    (async () => {
      const all: AgentSkill[] = [];
      let total = 1;
      while (all.length < total) {
        const page = await skillsApi.skills(all.length, controller.signal);
        all.push(...page.items);
        total = page.total;
        if (!page.items.length) break;
      }
      if (!controller.signal.aborted) setItems(all);
    })()
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    setDetail(null);
    setPreviewIssue('');
    if (!previewId) return;
    const controller = new AbortController();
    skillsApi
      .skill(previewId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDetail(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setPreviewIssue(errorMessage(cause));
      });
    return () => controller.abort();
  }, [previewId, previewRevision]);
  const visible = items.filter((item) => item.builtin === (tab === 'builtin'));
  const done = (
    <Button type="primary" onClick={onClose}>
      完成
    </Button>
  );
  const management = (
    <a href="/skills" target="_blank" rel="noopener noreferrer">
      管理 Skill
    </a>
  );
  return (
    <Dialog
      title="加载 Skill"
      className={['agent-context-dialog', dialogClassName].filter(Boolean).join(' ')}
      onClose={onClose}
    >
      <div className="agent-context-dialog-body">
        <div className="agent-skill-heading">
          <Segmented
            aria-label="技能来源"
            value={tab}
            options={[
              { value: 'builtin', label: '内置技能' },
              { value: 'personal', label: '我的技能' },
            ]}
            onChange={(value) => setTab(value as typeof tab)}
          />
          <span>已加载 {selected.length} / 8</span>
        </div>
        {error && (
          <div role="alert" className="agent-context-empty">
            <p>{error}</p>
            <Button size="small" onClick={() => setRevision((value) => value + 1)}>
              重新载入
            </Button>
          </div>
        )}
        {loading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : visible.length ? (
          <div className="agent-skill-list">
            {visible.map((skill) => {
              const chosen = selected.some((item) => item.id === skill.id);
              return (
                <article key={skill.id}>
                  <Checkbox
                    checked={chosen}
                    disabled={disabled || (!chosen && (!skill.enabled || !skill.compatible || selected.length >= 8))}
                    onChange={(event) =>
                      onChange(
                        event.target.checked ? [...selected, skill] : selected.filter((item) => item.id !== skill.id),
                      )
                    }
                  >
                    {skill.name}
                  </Checkbox>
                  <span>{skill.enabled ? skill.description : '已停用'}</span>
                  <Button
                    size="small"
                    onClick={() => {
                      setDetail(null);
                      setPreviewId(skill.id);
                    }}
                  >
                    查看
                  </Button>
                </article>
              );
            })}
          </div>
        ) : (
          !error && (
            <p className="agent-context-empty">{tab === 'personal' ? '还没有个人 Skill。' : '暂无内置 Skill。'}</p>
          )
        )}
        {previewId && (
          <section className="agent-skill-preview" aria-label="Skill 文件预览">
            <div className="agent-skill-heading">
              <span>{detail?.name ?? '文件预览'}</span>
              <Button size="small" onClick={() => setPreviewId(null)}>
                关闭查看
              </Button>
            </div>
            {previewIssue ? (
              <div role="alert">
                <p>{previewIssue}</p>
                <Button onClick={() => setPreviewRevision((value) => value + 1)}>重试</Button>
              </div>
            ) : !detail ? (
              <Skeleton active paragraph={{ rows: 4 }} />
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
          </section>
        )}
        {layout !== 'standard' && (
          <div className="agent-context-dialog-actions">
            {management}
            {done}
          </div>
        )}
      </div>
      {layout === 'standard' && (
        <footer className="standard-assistant-dialog-footer">
          {management}
          {done}
        </footer>
      )}
    </Dialog>
  );
}
