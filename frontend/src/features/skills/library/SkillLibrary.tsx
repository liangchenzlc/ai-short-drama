import { errorMessage } from '@host/api/http';
import { skillsApi } from '@host/api/skills/catalog';
import { safeCanvasReturn } from '@host/features/ai-config/editor/config-form-model';
import { workflowStorage } from '@host/features/identity/storage/account-storage';
import { SkillDetailDialog } from '@host/features/skills/details/SkillDetailDialog';
import { SkillImportDialog } from '@host/features/skills/imports/SkillImportDialog';
import {
  clearSkillDraft,
  downloadSkillText,
  rawSkillAction,
  rawSkillDraft,
  readSkillDraft,
  saveSkillDraft,
} from '@host/features/skills/imports/skill-draft';
import { modeLabel } from '@host/features/skills/library/skill-model';
import { useSkillActions } from '@host/features/skills/library/useSkillActions';
import { SkillFileBrowser } from '@host/features/skills/resources/SkillFileBrowser';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback, StudioMessageProvider, useStudioMessage } from '@host/shared/ui/StudioFeedback';
import { PageHeader } from '@host/shared/ui/Workspace';
import { confirmAction } from '@host/shared/ui/confirm';
import type { AgentSkill, AgentSkillDetail, AgentSkillFilters } from '@host/types/assistant/engine';
import type { SkillDraft } from '@host/types/skills/imports/skill-draft';
import { Button, Input, Select, Skeleton, Switch, Tabs } from 'antd';
import { Search, Upload } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import './skills.css';
export function SkillLibrary({ accountId }: { accountId: string }) {
  const host = useRef<HTMLDivElement>(null);
  return (
    <div ref={host} className="studio-feedback-host">
      <StudioMessageProvider local getContainer={() => host.current ?? document.body}>
        <SkillLibraryBody key={accountId} accountId={accountId} />
      </StudioMessageProvider>
    </div>
  );
}
function SkillLibraryBody({ accountId }: { accountId: string }) {
  const [params, setParams] = useSearchParams();
  const source = params.get('tab') === 'builtin' ? 'builtin' : 'personal';
  const search = params.get('q')?.slice(0, 120) ?? '';
  const status = ['enabled', 'disabled'].includes(params.get('status') ?? '') ? params.get('status')! : 'all';
  const mode = ['standard', 'infinite_canvas'].includes(params.get('mode') ?? '') ? params.get('mode')! : 'all';
  const pageNumber = Math.max(1, Math.min(100000, Number(params.get('page')) || 1));
  const offset = (Math.floor(pageNumber) - 1) * 50;
  const [query, setQuery] = useState(search);
  const [list, setList] = useState<{
    items: AgentSkill[];
    total: number;
    loading: boolean;
    issue: string;
  }>({ items: [], total: 0, loading: true, issue: '' });
  const [counts, setCounts] = useState<{ personal?: number; builtin?: number }>({});
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<AgentSkill | null>(null);
  const [storage] = useState(workflowStorage);
  const [restored] = useState(() => readSkillDraft(accountId, storage));
  const [draft, setDraft] = useState(restored.draft);
  const [storageIssue, setStorageIssue] = useState(restored.error);
  const [importing, setImporting] = useState(false);
  const [target, setTarget] = useState<AgentSkillDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState('');
  const [highlight, setHighlight] = useState<string | null>(null);
  const live = useRef(true);
  const exportLock = useRef(false);
  const message = useStudioMessage();
  const actions = useSkillActions(accountId, (action, detail) => {
    setRevision((value) => value + 1);
    if (detail) setSelected((current) => (current?.id === detail.id ? detail : current));
    else if (action.kind === 'delete') setSelected(null);
    message.success(action.kind === 'delete' ? '已删除 Skill' : action.kind === 'copy' ? '已复制 Skill' : '设置已保存');
  });
  const blocked = busy || actions.busy || !!actions.pending || !!actions.storageError;
  const returnTo = safeCanvasReturn(params.get('return_to'));
  const filtersActive = !!search || status !== 'all' || mode !== 'all';
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  useEffect(() => {
    setQuery(search);
  }, [search]);
  function updateParams(change: Record<string, string | null>) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete('tags');
        for (const [key, value] of Object.entries(change)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        return next;
      },
      { replace: true },
    );
  }
  useEffect(() => {
    if (query.trim() === search) return;
    const timer = window.setTimeout(() => updateParams({ q: query.trim() || null, page: null }), query ? 300 : 0);
    return () => clearTimeout(timer);
  }, [query, search]);
  useEffect(() => {
    const controller = new AbortController();
    setList((current) => ({ ...current, loading: true, issue: '' }));
    const filters: AgentSkillFilters = {
      source,
      query: search || undefined,
      enabled: status === 'all' ? undefined : status === 'enabled',
      mode: mode === 'all' ? undefined : (mode as AgentSkillFilters['mode']),
    };
    skillsApi
      .skills(offset, controller.signal, filters)
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.items.length && offset > 0) {
          updateParams({
            page: String(Math.max(1, Math.ceil(result.total / 50))),
          });
          return;
        }
        setList({
          items: result.items,
          total: result.total,
          loading: false,
          issue: '',
        });
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setList((current) => ({
            ...current,
            loading: false,
            issue: errorMessage(cause),
          }));
      });
    return () => controller.abort();
  }, [source, search, status, mode, offset, revision]);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled(
      ['personal', 'builtin'].map(async (value) => {
        const key = value as 'personal' | 'builtin';
        const total = await skillsApi.skillCount(key, controller.signal);
        if (!controller.signal.aborted) setCounts((current) => ({ ...current, [key]: total }));
      }),
    );
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    if (!highlight || list.loading) return;
    document.getElementById(`skill-${highlight}`)?.scrollIntoView({ block: 'nearest' });
    const timer = setTimeout(() => setHighlight(null), 2500);
    return () => clearTimeout(timer);
  }, [highlight, list.loading]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (busy || actions.busy) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', unload);
    return () => window.removeEventListener('beforeunload', unload);
  }, [busy, actions.busy]);
  const loaded = useCallback((detail: AgentSkillDetail) => setSelected(detail), []);
  function beginImport(detail: AgentSkillDetail | null = null) {
    if (blocked) return;
    if (draft || storageIssue) {
      setIssue('请先处理已有导入记录');
      return;
    }
    setTarget(detail);
    setSelected(null);
    setImporting(true);
    setIssue('');
  }
  function persist(value: SkillDraft, settled = false) {
    const saved = saveSkillDraft(accountId, value, storage, settled);
    setDraft(value);
    setStorageIssue(saved ? '' : '导入记录未能保存，请下载记录或恢复浏览器存储');
    return saved;
  }
  function saved(detail: AgentSkillDetail) {
    if (!clearSkillDraft(accountId, storage)) {
      setStorageIssue('已收到成功回执，本机记录未清除，请继续核对原请求');
      return;
    }
    setDraft(null);
    setImporting(false);
    setBusy(false);
    setTarget(null);
    setStorageIssue('');
    setRevision((value) => value + 1);
    setQuery('');
    updateParams({ tab: null, q: null, status: null, mode: null, page: null });
    setHighlight(detail.id);
    message.success(`已${target || draft?.base ? '更新' : '导入'}「${detail.name}」`);
  }
  async function discardDraft() {
    if (
      busy ||
      !(await confirmAction('请先下载需要保留的记录。清除后不会删除服务端 Skill，也不会撤销已受理请求。', {
        title: '清除导入记录',
        confirmText: '明确清除',
      }))
    )
      return;
    if (clearSkillDraft(accountId, storage)) {
      setDraft(null);
      setStorageIssue('');
      setImporting(false);
    } else setStorageIssue('记录未能清除，请恢复浏览器存储后重试');
  }
  async function exportItem(item: AgentSkill) {
    if (exportLock.current || blocked) return;
    exportLock.current = true;
    setBusy(true);
    setIssue('');
    try {
      const file = await skillsApi.exportSkill(item.id, item.content_version);
      if (live.current) downloadSkillText(`${item.slug}.zip`, file);
    } catch (cause) {
      if (live.current) setIssue(errorMessage(cause));
    } finally {
      exportLock.current = false;
      if (live.current) setBusy(false);
    }
  }
  async function deleteItem(item: AgentSkill) {
    if (
      blocked ||
      !(await confirmAction(`删除「${item.name}」？已加载的任务继续使用原版本。`, {
        title: '删除 Skill',
        confirmText: '删除',
      }))
    )
      return;
    actions.start({ kind: 'delete', item });
  }
  function clearFilters() {
    setQuery('');
    updateParams({ q: null, status: null, mode: null, page: null });
  }
  return (
    <section className="studio-page skills-page" aria-labelledby="skills-title">
      <PageHeader
        id="skills-title"
        title="Skill 管理"
        actions={
          <>
            {returnTo && <Button onClick={() => window.location.assign(returnTo)}>返回画布</Button>}
            <Button type="primary" icon={<Upload size={16} />} disabled={blocked} onClick={() => beginImport()}>
              导入 Skill
            </Button>
          </>
        }
      />
      {(draft || storageIssue || actions.pending || actions.storageError) && (
        <div className="skill-recovery-entry">
          {(draft || storageIssue) && (
            <Button
              size="small"
              disabled={busy}
              onClick={() => {
                setTarget(draft?.base ?? null);
                setImporting(true);
              }}
            >
              恢复{draft?.imported ? '导入' : '旧 Skill'}记录
            </Button>
          )}
          {(actions.pending || actions.storageError) && (
            <Button size="small" disabled={actions.busy} onClick={() => actions.setOpen(true)}>
              核对待处理操作
            </Button>
          )}
        </div>
      )}
      {issue && <StudioFeedback type="error" message={issue} noticeKey="skill-library" />}
      <Tabs
        activeKey={source}
        onChange={(value) =>
          updateParams({
            tab: value === 'builtin' ? 'builtin' : null,
            page: null,
          })
        }
        items={(['personal', 'builtin'] as const).map((value) => ({
          key: value,
          label: (
            <>
              {value === 'personal' ? '我的 Skill' : '内置 Skill'}
              {counts[value] !== undefined && <span className="skill-tab-count">{counts[value]}</span>}
            </>
          ),
        }))}
      />
      <div className="skill-toolbar">
        <Input
          aria-label="搜索 Skill"
          prefix={<Search size={15} />}
          placeholder="搜索名称或用途"
          value={query}
          maxLength={120}
          allowClear
          onChange={(event) => setQuery(event.target.value)}
        />
        <Select
          aria-label="Skill 启用状态"
          value={status}
          onChange={(value) => updateParams({ status: value === 'all' ? null : value, page: null })}
          options={[
            { value: 'all', label: '全部状态' },
            { value: 'enabled', label: '已启用' },
            { value: 'disabled', label: '已停用' },
          ]}
        />
        <Select
          aria-label="Skill 适用模式筛选"
          value={mode}
          onChange={(value) => updateParams({ mode: value === 'all' ? null : value, page: null })}
          options={[
            { value: 'all', label: '全部模式' },
            { value: 'standard', label: '创作可用' },
            { value: 'infinite_canvas', label: '画布可用' },
          ]}
        />
      </div>
      {filtersActive && !list.loading && <p className="skill-result-count">找到 {list.total} 个 Skill</p>}
      {list.loading ? (
        <div className="skill-card-grid" role="status" aria-label="正在读取 Skill">
          {[0, 1, 2].map((value) => (
            <Skeleton key={value} active className="skill-card-skeleton" />
          ))}
        </div>
      ) : list.issue ? (
        <div className="studio-empty" role="alert">
          <p>{list.issue}</p>
          <Button onClick={() => setRevision((value) => value + 1)}>重试</Button>
        </div>
      ) : !list.items.length ? (
        <div className="studio-empty">
          <h2>{filtersActive ? '没有匹配的 Skill' : source === 'personal' ? '还没有 Skill' : '暂无内置 Skill'}</h2>
          {filtersActive ? (
            <Button onClick={clearFilters}>清除筛选</Button>
          ) : (
            source === 'personal' && (
              <>
                <Button type="primary" disabled={blocked} onClick={() => beginImport()}>
                  导入 Skill
                </Button>
                <Button type="text" onClick={() => updateParams({ tab: 'builtin', page: null })}>
                  查看内置 Skill
                </Button>
              </>
            )
          )}
        </div>
      ) : (
        <div className="skill-card-grid">
          {list.items.map((item) => (
            <article
              key={item.id}
              id={`skill-${item.id}`}
              className={`skill-card${highlight === item.id ? ' highlighted' : ''}`}
            >
              <button className="skill-card-open" aria-label={`查看 ${item.name}`} onClick={() => setSelected(item)}>
                <h2 title={item.name}>{item.name}</h2>
                <p>{item.compatible ? item.description : item.issues[0] || '此 Skill 暂不可用'}</p>
                <span className="skill-card-mode">{modeLabel(item.modes)}</span>
              </button>
              <Switch
                className="skill-card-switch"
                aria-label={`启用 ${item.name}`}
                checked={item.enabled}
                loading={actions.busy && actions.pending?.kind === 'settings' && actions.pending.item.id === item.id}
                disabled={blocked || !!draft?.pending || (!item.enabled && !item.compatible)}
                onChange={(value) => actions.start({ kind: 'settings', item, enabled: value })}
              />
            </article>
          ))}
        </div>
      )}
      {list.total > 50 && (
        <nav className="skill-pagination" aria-label="Skill 分页">
          <span>
            第 {Math.floor(offset / 50) + 1} / {Math.ceil(list.total / 50)} 页
          </span>
          <Button
            disabled={!offset || list.loading}
            onClick={() => updateParams({ page: String(Math.max(1, offset / 50)) })}
          >
            上一页
          </Button>
          <Button
            disabled={offset + 50 >= list.total || list.loading}
            onClick={() => updateParams({ page: String(offset / 50 + 2) })}
          >
            下一页
          </Button>
        </nav>
      )}
      {selected && (
        <SkillDetailDialog
          key={selected.id}
          accountId={accountId}
          item={selected}
          disabled={blocked || !!draft?.pending}
          onClose={() => setSelected(null)}
          onLoaded={loaded}
          onUpdate={beginImport}
          onExport={(item) => void exportItem(item)}
          onDelete={(item) => void deleteItem(item)}
          onEnabled={(item, value) => actions.start({ kind: 'settings', item, enabled: value })}
          onMode={(item, modes) => actions.start({ kind: 'mode', item, modes })}
        />
      )}
      {importing && (!storageIssue || !!draft) && (
        <SkillImportDialog
          accountId={accountId}
          target={target}
          restored={draft}
          sameNames={list.items.map((item) => item.name)}
          storageIssue={storageIssue}
          onPersist={persist}
          onSaved={saved}
          onBusy={setBusy}
          onClose={() => setImporting(false)}
          onDiscard={() => void discardDraft()}
        />
      )}
      {importing && storageIssue && !draft && (
        <Dialog title="恢复 Skill 记录" onClose={() => setImporting(false)}>
          <p>{storageIssue}</p>
          <footer className="skill-dialog-footer">
            <Button onClick={() => downloadSkillText('skill-recovery.json', rawSkillDraft(accountId, storage))}>
              下载原记录
            </Button>
            <Button danger onClick={() => void discardDraft()}>
              明确清除
            </Button>
          </footer>
        </Dialog>
      )}
      {actions.open && (
        <Dialog
          title="核对 Skill 操作"
          className="skill-preview-dialog"
          onClose={() => actions.setOpen(false)}
          canClose={!actions.busy}
        >
          <div className="skill-preview-summary">
            <p role="status">{actions.issue}</p>
            {actions.pending && <p>{actions.pending.item.name}</p>}
            {actions.latest && (
              <p>
                当前模式：{modeLabel(actions.latest.modes)}，{actions.latest.enabled ? '已启用' : '已停用'}
              </p>
            )}
          </div>
          {actions.latest && actions.pending?.kind === 'delete' && (
            <SkillFileBrowser
              identity={`${accountId}:${actions.latest.id}:${actions.latest.content_version}:recovery`}
              skillId={actions.latest.id}
              instructions={actions.latest.instructions}
              resources={actions.latest.resources.map((item) => ({
                ...item,
                content: null,
                origin_version: actions.latest!.content_version,
              }))}
            />
          )}
          <footer className="skill-dialog-footer">
            <Button
              disabled={actions.busy}
              onClick={() => downloadSkillText('skill-operation.json', rawSkillAction(accountId, storage))}
            >
              下载记录
            </Button>
            <Button danger disabled={actions.busy} onClick={() => void actions.discard()}>
              明确清除
            </Button>
            <span className="skill-footer-spacer" />
            {actions.pending && (
              <Button loading={actions.busy} onClick={() => void actions.reconcile()}>
                核对原操作
              </Button>
            )}
            {actions.latest && (
              <Button disabled={actions.busy} onClick={() => void actions.reapply()}>
                按此版本继续
              </Button>
            )}
          </footer>
        </Dialog>
      )}
    </section>
  );
}
