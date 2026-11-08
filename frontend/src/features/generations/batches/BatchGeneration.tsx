import { useBatchSelectionState } from '@host/features/generations/batches/useBatchSelectionState';
import { findSubmission, notifyBatchSubmissionChanged, readSubmission, writeSubmission } from '@host/features/generations/batches/batchSubmission';
import type { BatchSubmission, SavedBatchSubmission } from '@host/features/generations/batches/batchSubmission';
import { generationBatches } from '@host/api/generations/batches/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { ConfigSelect } from '@host/features/ai-config/selection/ModelSelect';
import { TaskDetail } from '@host/features/generations/tasks/TaskDetail';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import { projectPath } from '@host/shared/navigation/paths';
import { confirmAction } from '@host/shared/ui/confirm';
import { StudioFeedback, StudioFeedbackIndicator, StudioMessageProvider } from '@host/shared/ui/StudioFeedback';
import type {
  BatchDetail,
  BatchPreview,
  BatchRequest,
  BatchScene,
  BatchScope,
} from '@host/types/generations/batches/requests';
import {
  Alert,
  Button,
  Checkbox,
  ConfigProvider,
  Drawer,
  Input,
  Pagination,
  Radio,
  Select,
  Spin,
  Table,
  Tag,
  Tooltip,
} from 'antd';
import { Clapperboard, Images } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import './batches.css';

type PreparedBatch = { body: BatchRequest; preview: BatchPreview };

const labels: Record<string, string> = {
  asset_image: '素材图片',
  shot_image: '分镜图片',
  shot_video: '分镜视频',
  waiting: '等待执行',
  active: '执行中',
  running: '执行中',
  paused: '已暂停',
  blocked: '依赖待处理',
  needs_review: '受理待核对',
  succeeded: '成功',
  partial: '部分成功',
  failed: '失败',
  cancelled: '已取消',
  ready: '可执行',
  adopted: '已有采用',
  review: '待审核',
};
const ongoing = (status: string) => ['running', 'paused', 'needs_review', 'cancelled'].includes(status);

export function useBatchSelection(scope: string) {
  const [enabled, setEnabled] = useState(false);
  const selection = useBatchSelectionState(scope);
  useEffect(() => {
    const controller = new AbortController();
    void generationBatches
      .capabilities(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setEnabled(result.enabled);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);
  return { enabled, ...selection };
}
export function BatchLauncher({
  scope,
  selection,
  loadedIds,
  disabled,
  beforePreflight,
  presentation,
  compactScene,
}: {
  scope: BatchScope;
  selection: ReturnType<typeof useBatchSelection>;
  loadedIds: string[];
  disabled?: boolean;
  beforePreflight?: () => Promise<boolean>;
  presentation?: 'standard-creation';
  compactScene?: BatchScene;
}) {
  const [open, setOpen] = useState(false),
    [all, setAll] = useState(false);
  const [restore, setRestore] = useState(false);
  const [saved, setSaved] = useState<SavedBatchSubmission | null>(null);
  const scopeIdentity = JSON.stringify(scope);
  useEffect(() => {
    if (compactScene && !open) setSaved(findSubmission(scope, compactScene));
  }, [compactScene, open, scopeIdentity]);
  if (!selection.enabled) return null;
  if (compactScene) {
    const label = compactScene === 'shot_video' ? '批量生成视频' : compactScene === 'shot_image' ? '批量生成分镜图' : '批量生成图片';
    const pending = saved && (!!saved.submission.pendingRetry || saved.submission.parts.some((part) => !part.id));
    return <>
      <Tooltip title={pending ? '请先核对上次批次' : label}><span><Button type="text" size="small" aria-label={label} disabled={disabled || !selection.ids.length || !!pending}
        icon={compactScene === 'shot_video' ? <Clapperboard size={16} /> : <Images size={16} />} onClick={() => { setRestore(false); setOpen(true); }} /></span></Tooltip>
      {saved && <Button type="text" size="small" onClick={() => { setRestore(true); setOpen(true); }}>
        {pending ? '继续核对批次' : '查看上次批次'}
      </Button>}
      {open && <BatchCreatePanel key={restore ? saved?.scopeKey : 'new'} presentation={presentation} scope={scope} sourceIds={selection.ids}
        beforePreflight={beforePreflight} initialScene={compactScene} restoreSaved={restore} savedScopeKey={restore ? saved?.scopeKey : undefined}
        onStartNew={() => setRestore(false)} onClose={() => setOpen(false)} />}
    </>;
  }
  return (
    <div className="batch-toolbar">
      <Checkbox
        disabled={disabled || !loadedIds.length}
        checked={!!loadedIds.length && loadedIds.every((id) => selection.ids.includes(id))}
        indeterminate={
          loadedIds.some((id) => selection.ids.includes(id)) && !loadedIds.every((id) => selection.ids.includes(id))
        }
        onChange={(event) =>
          selection.setIds(
            event.target.checked
              ? [...new Set([...selection.ids, ...loadedIds])]
              : selection.ids.filter((id) => !loadedIds.includes(id)),
          )
        }
      >
        选择已加载项
      </Checkbox>
      <span>已选 {selection.ids.length} 项</span>
      <Button
        disabled={disabled || !selection.ids.length}
        onClick={() => {
          setAll(false);
          setOpen(true);
        }}
      >
        批量生成所选
      </Button>
      <Button
        disabled={disabled}
        onClick={() => {
          setAll(true);
          setOpen(true);
        }}
      >
        预检当前范围全部
      </Button>
      {!!selection.ids.length && (
        <Button type="text" onClick={() => selection.setIds([])}>
          清空选择
        </Button>
      )}
      {open && (
        <BatchCreatePanel
          presentation={presentation}
          scope={scope}
          sourceIds={all ? undefined : selection.ids}
          beforePreflight={beforePreflight}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

function BatchCreatePanel({
  scope,
  sourceIds,
  beforePreflight,
  onClose,
  presentation,
  initialScene,
  restoreSaved = true,
  savedScopeKey,
  onStartNew,
}: {
  scope: BatchScope;
  sourceIds?: string[];
  beforePreflight?: () => Promise<boolean>;
  onClose: () => void;
  presentation?: 'standard-creation';
  initialScene?: BatchScene;
  restoreSaved?: boolean;
  savedScopeKey?: string;
  onStartNew?: () => void;
}) {
  const assets = initialScene === 'asset_image' || !!scope.asset_kind;
  const [scene, setScene] = useState<BatchScene>(initialScene ?? (assets ? 'asset_image' : 'shot_image'));
  const [config, setConfig] = useState<string>(),
    [count, setCount] = useState(1);
  const [mode, setMode] = useState<'missing' | 'regenerate'>('missing');
  const [aspect, setAspect] = useState<string>(),
    [resolution, setResolution] = useState('');
  const [previews, setPreviews] = useState<PreparedBatch[]>([]);
  const [accepted, setAccepted] = useState<string[]>([]),
    [batch, setBatch] = useState<string>();
  const [submission, setSubmission] = useState<BatchSubmission | null>(null);
  const [reviewRemaining, setReviewRemaining] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const lock = useRef(false),
    mounted = useRef(true);
  const request = useRef<AbortController | null>(null);
  const feedbackHost = useRef<HTMLDivElement>(null);
  const [newScopeKey] = useState(() => !restoreSaved && initialScene
    ? `batch:${JSON.stringify(scope)}:${initialScene}:${crypto.randomUUID()}` : undefined);
  const scopeKey = savedScopeKey ?? newScopeKey ?? `batch:${JSON.stringify(scope)}:${scene}`;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      request.current?.abort();
    };
  }, []);
  useEffect(() => {
    const saved = restoreSaved ? readSubmission(scopeKey) : null;
    setSubmission(saved);
    setBatch(saved?.pendingRetry?.batchId ?? (saved?.parts.every((part) => part.id) ? saved.parts[0]?.id : undefined));
  }, [scopeKey, restoreSaved]);
  function saveSubmission(next: BatchSubmission | null) {
    writeSubmission(scopeKey, next);
    if (mounted.current) setSubmission(next);
  }
  function invalidate() {
    setPreviews([]);
    setError('');
  }
  async function preflight() {
    if (lock.current || !config) return;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      if (beforePreflight && !(await beforePreflight())) throw new Error('请先保存当前编辑，再预检批量生成。');
      const body: BatchRequest = {
        scene,
        scope,
        config_id: config,
        source_ids: sourceIds,
        mode,
        count: scene === 'shot_video' ? 1 : count,
        ...(assets
          ? {
              asset_parameters: {
                aspect,
                resolution: resolution.trim() || undefined,
              },
            }
          : {}),
      };
      request.current = new AbortController();
      const groups = sourceIds ? Array.from({ length: Math.ceil(sourceIds.length / 100) }, (_, index) => sourceIds.slice(index * 100, (index + 1) * 100)) : [undefined];
      const next: PreparedBatch[] = [];
      for (const ids of groups) {
        const part = { ...body, source_ids: ids };
        next.push({ body: part, preview: await generationBatches.preflight(part, request.current.signal) });
      }
      if (mounted.current) {
        setPreviews(next);
        setAccepted(next.flatMap(({ preview }) => preview.items.filter((item) => item.state === 'ready').map((item) => item.source_id)));
      }
    } catch (cause) {
      if (mounted.current) {
        invalidate();
        setError(errorMessage(cause));
      }
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function submit() {
    if (!previews.length || lock.current || !accepted.length) return;
    const next: BatchSubmission = {
      parts: previews.map(({ body, preview }) => ({
        value: { ...body, preflight_hash: preview.preflight_hash, accepted_ids: preview.items.filter((item) => accepted.includes(item.source_id)).map((item) => item.source_id) },
      })).filter((part) => part.value.accepted_ids.length),
      retryIds: [],
    };
    saveSubmission(next);
    await submitParts(next);
  }
  async function submitParts(initial: BatchSubmission) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    let next = initial;
    let currentIndex = -1;
    try {
      for (let index = 0; index < next.parts.length; index++) {
        if (next.parts[index].id) continue;
        currentIndex = index;
        next = { ...next, parts: next.parts.map((part, i) => i === index ? { ...part, started: true } : part) };
        saveSubmission(next);
        const attemptScope = `${scopeKey}:part:${index}`;
        const key = await requestAttempt(attemptScope, next.parts[index].value, attemptStorage());
        const created = await generationBatches.create(next.parts[index].value, key);
        next = { ...next, parts: next.parts.map((part, i) => i === index ? { ...part, id: created.id } : part) };
        saveSubmission(next);
        clearAttempt(attemptScope, attemptStorage());
        if (!mounted.current) return;
      }
      if (mounted.current) setBatch(next.parts[0]?.id);
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'batch_preflight_changed') {
        next = { ...next, parts: next.parts.map((part, i) => i === currentIndex ? { ...part, started: false, rejected: true } : part) };
        saveSubmission(next);
        clearAttempt(`${scopeKey}:part:${currentIndex}`, attemptStorage());
      }
      if (mounted.current) setError(errorMessage(cause));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function preflightRemaining(current: BatchSubmission) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      if (beforePreflight && !(await beforePreflight())) throw new Error('请先保存当前编辑，再预检批量生成。');
      request.current = new AbortController();
      const next: PreparedBatch[] = [];
      const selected: string[] = [];
      for (const part of current.parts.filter((item) => !item.id)) {
        const { preflight_hash: _hash, accepted_ids: previous, ...body } = part.value;
        const preview = await generationBatches.preflight(body, request.current.signal);
        next.push({ body, preview });
        selected.push(...preview.items.filter((item) => item.state === 'ready' && previous.includes(item.source_id)).map((item) => item.source_id));
      }
      if (mounted.current) {
        setPreviews(next);
        setAccepted(selected);
        setReviewRemaining(true);
      }
    } catch (cause) {
      if (mounted.current) setError(errorMessage(cause));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function submitRemaining(current: BatchSubmission) {
    const next: BatchSubmission = {
      ...current,
      parts: [...current.parts.filter((part) => part.id), ...previews.map(({ body, preview }) => ({
        value: { ...body, preflight_hash: preview.preflight_hash, accepted_ids: preview.items.filter((item) => accepted.includes(item.source_id)).map((item) => item.source_id) },
      })).filter((part) => part.value.accepted_ids.length)],
    };
    setReviewRemaining(false);
    saveSubmission(next);
    await submitParts(next);
  }
  async function finishRemaining(current: BatchSubmission) {
    if (!(await confirmAction('未提交的项目将不再生成，已创建的批次继续执行。确定结束？', {
      className: presentation ? 'standard-creation-dialog' : undefined,
    }))) return;
    const parts = current.parts.filter((part) => part.id);
    const next = parts.length ? { ...current, parts } : null;
    saveSubmission(next);
    setReviewRemaining(false);
    if (next) setBatch(next.parts[0].id);
    else invalidate();
  }
  const batchIds = submission ? [...submission.parts.flatMap((part) => part.id ? [part.id] : []), ...submission.retryIds] : [];
  const complete = !!submission && submission.parts.every((part) => part.id);
  if (batch && submission) return <BatchProgress presentation={presentation} id={batch} ids={batchIds}
    onClose={complete ? onClose : () => setBatch(undefined)}
    onBack={complete ? undefined : () => setBatch(undefined)}
    onNew={complete && !submission.pendingRetry && (!onStartNew || !!sourceIds?.length) ? () => {
      if (onStartNew) onStartNew();
      else { saveSubmission(null); setBatch(undefined); invalidate(); }
    } : undefined}
    pendingRetry={submission.pendingRetry}
    onRetryPending={(pendingRetry) => saveSubmission({ ...submission, pendingRetry })}
    onBatchCreated={(id) => saveSubmission({ ...submission, retryIds: [...submission.retryIds, id], pendingRetry: undefined })} />;
  return (
    <Drawer
      rootClassName={presentation ? 'standard-creation-drawer' : undefined}
      open
      title={
        presentation ? (
          <>
            {submission ? '批次提交' : '批量生成预检'}
            <StudioFeedbackIndicator />
          </>
        ) : (
          submission ? '批次提交' : '批量生成预检'
        )
      }
      width={800}
      onClose={() => !busy && onClose()}
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      drawerRender={
        presentation
          ? (node) => (
              <StudioMessageProvider local getContainer={() => feedbackHost.current ?? document.body}>
                <ConfigProvider getPopupContainer={() => feedbackHost.current ?? document.body}>
                  <div
                    ref={feedbackHost}
                    className="studio-feedback-host"
                    style={{
                      height: '100%',
                      position: 'relative',
                      pointerEvents: 'auto',
                    }}
                  >
                    {node}
                  </div>
                </ConfigProvider>
              </StudioMessageProvider>
            )
          : undefined
      }
    >
      <div className="batch-form">
        {submission ? (
          <>
            <p>每批最多 100 项。受理待核对的批次将使用原请求继续提交。</p>
            {submission.parts.map((part, index) => (
              <div className="batch-toolbar" key={index}>
                <span>第 {index + 1} 批 · {part.id ? '已创建' : part.rejected ? '预检已失效' : part.started ? '受理待核对' : '待提交'}</span>
                {part.id && <Button onClick={() => setBatch(part.id)}>查看第 {index + 1} 批</Button>}
              </div>
            ))}
            {error && (presentation ? (
              <StudioFeedback type="error" title="批次提交" noticeKey={`${scopeKey}:submit`} message={error}
                description="提交结果不明时保留当前预检直接重试，使用同一请求标识核对；来源冲突时请重新预检。" />
            ) : (
              <Alert type="error" showIcon message={error}
                description="提交结果不明时保留当前预检直接重试，使用同一请求标识核对；来源冲突时请重新预检。" />
            ))}
            {submission.parts.some((part) => part.rejected) ? (
              <>
                {!reviewRemaining && <Button loading={busy} onClick={() => void preflightRemaining(submission)}>重新预检未提交项</Button>}
                {reviewRemaining && <>
                  <BatchPreviewTable previews={previews} accepted={accepted} setAccepted={setAccepted} busy={busy} />
                  <Button type="primary" loading={busy} disabled={!accepted.length} onClick={() => void submitRemaining(submission)}>
                    确认并继续提交 {accepted.length} 项
                  </Button>
                </>}
                <Button disabled={busy} onClick={() => void finishRemaining(submission)}>结束未提交项</Button>
              </>
            ) : <Button type="primary" loading={busy} onClick={() => void submitParts(submission)}>继续提交</Button>}
          </>
        ) : (<>
        <p>
          {sourceIds ? `已选择 ${sourceIds.length} 项` : '服务端将读取当前作品或素材分类的全部匹配项'}
          。单批最多 100 项{sourceIds && sourceIds.length > 100 ? '，将自动分批' : ''}。
          {scene === 'shot_video'
            ? '视频保存为候选，请在来源作品中核对并主动采用。'
            : '生成图片保存为候选，请在来源作品中核对并主动设为主图。'}
        </p>
        {!assets && !initialScene && (
          <label>
            生成类型
            <Select
              aria-label="生成类型"
              classNames={presentation ? { popup: { root: 'standard-creation-popup' } } : undefined}
              value={scene}
              disabled={busy}
              options={['shot_image', 'shot_video'].map((value) => ({
                value,
                label: labels[value],
              }))}
              onChange={(value) => {
                setScene(value);
                setConfig(undefined);
                invalidate();
              }}
            />
          </label>
        )}
        <label>
          生成模型
          <ConfigSelect
            presentation={presentation}
            key={scene}
            kind={scene === 'shot_video' ? 'video' : 'image'}
            autoDefault={false}
            value={config}
            disabled={busy}
            onChange={(value) => {
              setConfig(value);
              invalidate();
            }}
          />
        </label>
        <Radio.Group
          value={mode}
          disabled={busy}
          onChange={(event) => {
            setMode(event.target.value);
            invalidate();
          }}
        >
          <Radio value="missing">仅生成缺失项</Radio>
          <Radio value="regenerate">明确重新生成（可能再次计费）</Radio>
        </Radio.Group>
        {scene !== 'shot_video' && (
          <label>
            每项图片数量
            <Select
              classNames={presentation ? { popup: { root: 'standard-creation-popup' } } : undefined}
              value={count}
              disabled={busy}
              options={[1, 2, 3, 4].map((value) => ({
                value,
                label: `${value} 张`,
              }))}
              onChange={(value) => {
                setCount(value);
                invalidate();
              }}
            />
          </label>
        )}
        {assets && (
          <div className="batch-fields">
            <label>
              画幅
              <Select
                classNames={presentation ? { popup: { root: 'standard-creation-popup' } } : undefined}
                value={aspect}
                disabled={busy}
                allowClear
                placeholder="模型默认"
                options={['16:9', '9:16', '1:1', '4:3', '3:4'].map((value) => ({
                  value,
                  label: value,
                }))}
                onChange={(value) => {
                  setAspect(value);
                  invalidate();
                }}
              />
            </label>
            <label>
              清晰度
              <Input
                value={resolution}
                maxLength={32}
                disabled={busy}
                placeholder="模型默认"
                onChange={(event) => {
                  setResolution(event.target.value);
                  invalidate();
                }}
              />
            </label>
          </div>
        )}
        {!assets && <p>使用各分镜已保存的画幅、清晰度、布局及视频时长。预检会等待本页编辑保存。</p>}
        <Button loading={busy} disabled={!config} onClick={() => void preflight()}>
          检查可执行项
        </Button>
        {error &&
          (presentation ? (
            <StudioFeedback
              type="error"
              title="批量生成预检"
              noticeKey={`${scopeKey}:submit`}
              message={error}
              description="提交结果不明时保留当前预检直接重试，使用同一请求标识核对；来源冲突时请重新预检。"
            />
          ) : (
            <Alert
              type="error"
              showIcon
              message={error}
              description="提交结果不明时保留当前预检直接重试，使用同一请求标识核对；来源冲突时请重新预检。"
            />
          ))}
        {!!previews.length && (
          <>
            <BatchPreviewTable previews={previews} accepted={accepted} setAccepted={setAccepted} busy={busy} />
            <p role="status">
              将创建 {accepted.length} 个任务，最多请求 {accepted.length * (scene === 'shot_video' ? 1 : count)}{' '}
              个结果。此模型批次并发上限 {previews[0].preview.concurrency}，不含手动单任务。
            </p>
            <Button type="primary" loading={busy} disabled={!accepted.length} onClick={() => void submit()}>
              确认并启动 {accepted.length} 项生成
            </Button>
          </>
        )}
        </>)}
      </div>
    </Drawer>
  );
}

function BatchPreviewTable({ previews, accepted, setAccepted, busy }: {
  previews: PreparedBatch[];
  accepted: string[];
  setAccepted: (ids: string[]) => void;
  busy: boolean;
}) {
  return <Table
    size="small"
    rowKey="source_id"
    dataSource={previews.flatMap(({ preview }) => preview.items)}
    pagination={false}
    scroll={{ y: 320, x: 500 }}
    rowSelection={{
      selectedRowKeys: accepted,
      onChange: (keys) => setAccepted(keys.map(String)),
      getCheckboxProps: (item) => ({ disabled: item.state !== 'ready' || busy }),
    }}
    columns={[
      { title: '对象', dataIndex: 'name' },
      { title: '状态', render: (_, item) => labels[item.state] ?? item.state },
      {
        title: '说明与参数',
        render: (_, item) => item.reason || Object.entries(item.parameters ?? {}).map(([key, value]) =>
          `${({ aspect: '画幅', resolution: '清晰度', count: '数量', duration_ms: '时长(ms)' } as Record<string, string>)[key] ?? key}: ${value}`,
        ).join(' / '),
      },
    ]}
  />;
}

export function BatchProgress({
  id: initialId,
  ids,
  onClose,
  onBack,
  onNew,
  onBatchCreated,
  pendingRetry,
  onRetryPending,
  presentation,
}: {
  id: string;
  ids?: string[];
  onClose: () => void;
  onBack?: () => void;
  onNew?: () => void;
  onBatchCreated?: (id: string) => void;
  pendingRetry?: { batchId: string; itemIds: string[] };
  onRetryPending?: (pending: { batchId: string; itemIds: string[] }) => void;
  presentation?: 'standard-creation';
}) {
  const [id, setId] = useState(initialId);
  const [history, setHistory] = useState([initialId]);
  const [data, setData] = useState<BatchDetail | null>(null),
    [offset, setOffset] = useState(0),
    [revision, setRevision] = useState(0);
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [task, setTask] = useState<string>();
  const [actionError, setActionError] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const lock = useRef(false);
  const feedbackHost = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const next = await generationBatches.detail(id, offset, controller.signal);
        if (!controller.signal.aborted) {
          setData(next);
          setError('');
          if (ongoing(next.status)) timer = setTimeout(load, 4000);
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, offset, revision]);
  async function control(action: 'pause' | 'resume' | 'cancel' | 'retry') {
    if (lock.current) return;
    const retryItems = pendingRetry?.batchId === id ? pendingRetry.itemIds : selected;
    if (
      action === 'cancel' &&
      !(await confirmAction('停止未执行项，并请求取消运行中的任务；已完成结果保留。确定取消？', {
        className: presentation ? 'standard-creation-dialog' : undefined,
      }))
    )
      return;
    if (
      action === 'retry' &&
      !(await confirmAction(`重新生成所选 ${retryItems.length} 项可能再次计费，请核对原结果。确定继续？`, {
        className: presentation ? 'standard-creation-dialog' : undefined,
      }))
    )
      return;
    lock.current = true;
    setBusy(true);
    setError('');
    setActionError('');
    try {
      if (action === 'retry') {
        const scope = `batch-retry:${id}`;
        onRetryPending?.({ batchId: id, itemIds: retryItems });
        const key = await requestAttempt(scope, { item_ids: retryItems }, attemptStorage());
        const next = await generationBatches.retry(id, retryItems, key);
        setSelected([]);
        setHistory((current) => [...current, next.id]);
        onBatchCreated?.(next.id);
        clearAttempt(scope, attemptStorage());
        setId(next.id);
        setOffset(0);
        setData(null);
      } else await generationBatches.control(id, action);
      notifyBatchSubmissionChanged();
      setRevision((v) => v + 1);
    } catch (cause) {
      if (presentation) setActionError(errorMessage(cause));
      else setError(errorMessage(cause));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  const scope = data?.scope.scope;
  return (
    <Drawer
      rootClassName={presentation ? 'standard-creation-drawer' : undefined}
      open
      title={
        presentation ? (
          <>
            批量生成进度
            <StudioFeedbackIndicator />
          </>
        ) : (
          '批量生成进度'
        )
      }
      width={880}
      onClose={() => !busy && onClose()}
      closable={!busy}
      drawerRender={
        presentation
          ? (node) => (
              <StudioMessageProvider local getContainer={() => feedbackHost.current ?? document.body}>
                <ConfigProvider getPopupContainer={() => feedbackHost.current ?? document.body}>
                  <div
                    ref={feedbackHost}
                    className="studio-feedback-host"
                    style={{
                      height: '100%',
                      position: 'relative',
                      pointerEvents: 'auto',
                    }}
                  >
                    {node}
                  </div>
                </ConfigProvider>
              </StudioMessageProvider>
            )
          : undefined
      }
    >
      {error && (
        <Alert type="error" message={error} action={<Button onClick={() => setRevision((v) => v + 1)}>刷新</Button>} />
      )}
      {actionError && (
        <StudioFeedback
          type="error"
          title="批次操作"
          noticeKey={`batch-control:${id}`}
          message={actionError}
          actions={
            <Button
              onClick={() => {
                setActionError('');
                setRevision((v) => v + 1);
              }}
            >
              刷新
            </Button>
          }
        />
      )}
      <div className="batch-form">
          {(ids ?? history).length > 1 && <label>
            查看批次
            <Select aria-label="查看批次" value={id} disabled={busy}
              classNames={presentation ? { popup: { root: 'standard-creation-popup' } } : undefined}
              options={(ids ?? history).map((value, index) => ({ value, label: `第 ${index + 1} 批` }))}
              onChange={(value) => { setId(value); setData(null); setOffset(0); setSelected([]); setError(''); setActionError(''); }} />
          </label>}
          {onBack && <Button onClick={onBack}>返回提交</Button>}
          {onNew && <Button onClick={onNew}>新建批量生成</Button>}
          {pendingRetry && <Alert type="warning" showIcon message="重试请求受理待核对，请使用原请求继续核对。" />}
          {!data ? <Spin /> : <>
          <h2>
            {labels[data.scene]} · {labels[data.status]}
          </h2>
          <p>
            离开页面后任务继续。
            {data.scene === 'shot_video' ? '视频保存为候选，请在来源作品中核对并主动采用。' : '生成图片保存为候选，请在来源作品中核对并主动设为主图。'}
          </p>
          <div className="batch-toolbar">
            {Object.entries(data.counts).map(([state, count]) => (
              <Tag key={state}>
                {labels[state]} {count}
              </Tag>
            ))}
          </div>
          <div className="batch-toolbar">
            <Button
              disabled={busy || data.can_control === false || data.status !== 'running'}
              onClick={() => void control('pause')}
            >
              暂停后续任务
            </Button>
            <Button
              disabled={busy || data.can_control === false || !['paused', 'needs_review'].includes(data.status)}
              onClick={() => void control('resume')}
            >
              恢复批次
            </Button>
            <Button
              danger
              disabled={busy || data.can_cancel === false || !ongoing(data.status)}
              onClick={() => void control('cancel')}
            >
              取消批次
            </Button>
            <Button onClick={() => setRevision((v) => v + 1)}>刷新进度</Button>
          </div>
          {scope?.project_id && (
            <Link to={projectPath(scope.project_id, data.scene === 'asset_image' ? 'assets' : 'storyboard')}>
              {data.scene === 'shot_video' ? '打开来源作品审核视频' : '打开来源作品核对图片'}
            </Link>
          )}
          {data.status === 'needs_review' &&
            (presentation ? (
              <StudioFeedback
                type="warning"
                quiet
                title="批次状态"
                noticeKey={`batch-review:${id}`}
                message="有任务受理结果不明，后续派发已暂停。请进入任务详情核对，系统不会自动重发。"
              />
            ) : (
              <Alert
                type="warning"
                message="有任务受理结果不明，后续派发已暂停。请进入任务详情核对，系统不会自动重发。"
              />
            ))}
          <Table
            rowKey="id"
            dataSource={data.items}
            pagination={false}
            scroll={{ x: 620 }}
            rowSelection={{
              selectedRowKeys: pendingRetry?.batchId === id ? pendingRetry.itemIds : selected,
              onChange: (keys) => setSelected(keys.map(String)),
              preserveSelectedRowKeys: true,
              getCheckboxProps: (item) => ({
                disabled: busy || !!pendingRetry || !item.task.can_retry || item.task.can_resume,
              }),
            }}
            columns={[
              { title: '对象', dataIndex: 'name' },
              {
                title: '状态',
                render: (_, item) => (
                  <span>
                    {labels[item.status]}
                    {item.error?.message || item.task.error?.message
                      ? ` · ${item.error?.message || item.task.error?.message}`
                      : ''}
                  </span>
                ),
              },
              {
                title: '操作',
                render: (_, item) => (
                  <Button onClick={() => setTask(item.task_id)}>
                    {item.task.can_resume ? '安全恢复／详情' : '任务与结果'}
                  </Button>
                ),
              },
            ]}
          />
          <Pagination
            current={offset / 20 + 1}
            pageSize={20}
            total={data.total}
            showSizeChanger={false}
            onChange={(page) => setOffset((page - 1) * 20)}
          />
          <Button disabled={busy || (pendingRetry ? pendingRetry.batchId !== id : !selected.length)} onClick={() => void control('retry')}>
            {pendingRetry?.batchId === id ? '继续核对重试' : '重新生成所选失败项'}
          </Button>
          </>}
      </div>
      {task && (
        <TaskDetail
          presentation={presentation}
          id={task}
          onClose={() => setTask(undefined)}
          onChanged={() => { setRevision((v) => v + 1); notifyBatchSubmissionChanged(); }}
          onCreated={(receipt) => {
            setTask(receipt.generation_id);
            setRevision((v) => v + 1);
            notifyBatchSubmissionChanged();
          }}
        />
      )}
    </Drawer>
  );
}
