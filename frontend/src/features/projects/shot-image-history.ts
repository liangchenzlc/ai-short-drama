import type { AssetFilters, GenerationDetail, GenerationFilters, GenerationSummary, MediaAsset, Page } from '../../api/types/generations';

export interface ShotImageHistoryState {
  tasks: GenerationSummary[];
  candidates: MediaAsset[];
  loading: boolean;
  error: string;
  hasMoreTasks: boolean;
  hasMoreCandidates: boolean;
}

interface HistoryApi {
  listTasks: (filters: GenerationFilters, signal: AbortSignal) => Promise<Page<GenerationSummary>>;
  listCandidates: (filters: AssetFilters, signal: AbortSignal) => Promise<Page<MediaAsset>>;
  detail: (id: string, signal: AbortSignal) => Promise<GenerationDetail>;
}

interface Cursor { offset: number; total: number; head: string[] }
type Operation = 'refresh' | 'poll' | 'tasks' | 'candidates';
const PAGE_SIZE = 20;
const active = (task: GenerationSummary) => task.status === 'queued' || task.status === 'running';

export function emptyShotImageHistory(): ShotImageHistoryState {
  return { tasks: [], candidates: [], loading: false, error: '', hasMoreTasks: false, hasMoreCandidates: false };
}

function mergeById<Item extends { created_at?: string }>(current: Item[], incoming: Item[], identify: (item: Item) => string): Item[] {
  const merged = new Map(current.map(item => [identify(item), item]));
  for (const item of incoming) merged.set(identify(item), item);
  return [...merged.values()].sort((left, right) => {
    const time = Date.parse(right.created_at ?? '') - Date.parse(left.created_at ?? '');
    if (Number.isFinite(time) && time !== 0) return time;
    const leftId = identify(left);
    const rightId = identify(right);
    if (/^\d+$/.test(leftId) && /^\d+$/.test(rightId)) return BigInt(rightId) > BigInt(leftId) ? 1 : BigInt(rightId) < BigInt(leftId) ? -1 : 0;
    return rightId.localeCompare(leftId);
  });
}

function updateCursor(cursor: Cursor, ids: string[], total: number, first: boolean) {
  if (first) {
    const previousIndex = cursor.head.findIndex(id => ids.includes(id));
    const shift = previousIndex < 0 ? 0 : ids.indexOf(cursor.head[previousIndex]) - previousIndex;
    cursor.offset = previousIndex >= 0 && total - cursor.total === shift
      ? Math.max(ids.length, cursor.offset + shift) : ids.length;
    cursor.head = ids;
  } else {
    if (total !== cursor.total) cursor.head = [];
    cursor.offset += ids.length;
  }
  cursor.total = ids.length ? total : cursor.offset;
}

export function createShotImageHistory({
  shotId, api, onChange, describeError,
  schedule = (callback, delay) => setTimeout(callback, delay),
  cancel = timer => clearTimeout(timer),
}: {
  shotId: string;
  api: HistoryApi;
  onChange: (state: ShotImageHistoryState) => void;
  describeError: (cause: unknown) => string;
  schedule?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
  cancel?: (timer: ReturnType<typeof setTimeout>) => void;
}) {
  let state = emptyShotImageHistory();
  const taskCursor: Cursor = { offset: 0, total: 0, head: [] };
  const candidateCursor: Cursor = { offset: 0, total: 0, head: [] };
  let loadedCandidateEnd = 0;
  let controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let tail: Promise<void> = Promise.resolve();
  const pending = new Map<Operation, Promise<void>>();
  let pendingCount = 0;
  const taskId = (task: GenerationSummary) => task.generation_id;
  const assetId = (asset: MediaAsset) => asset.asset_id;
  const scoped = (item: GenerationSummary | MediaAsset) => item.source?.scene === 'shot_image' && item.source.shot_id === shotId;
  const validTask = (task: GenerationSummary) => task.service_type === 'image' && scoped(task);
  const validCandidate = (asset: MediaAsset) => asset.media_type === 'image' && scoped(asset);

  function publish(changes: Partial<ShotImageHistoryState>) {
    state = { ...state, ...changes, hasMoreTasks: taskCursor.offset < taskCursor.total, hasMoreCandidates: candidateCursor.offset < candidateCursor.total };
    onChange(state);
  }

  function mergeTasks(items: GenerationSummary[]) {
    publish({ tasks: mergeById(state.tasks, items.filter(validTask), taskId) });
  }

  function mergeCandidates(items: MediaAsset[]) {
    publish({ candidates: mergeById(state.candidates, items.filter(validCandidate), assetId) });
  }

  function clearTimer() {
    if (timer !== undefined) cancel(timer);
    timer = undefined;
  }

  async function execute(operation: Operation, signal: AbortSignal) {
    const refreshing = operation === 'refresh' || operation === 'poll';
    const errors: string[] = [];
    const attempt = async (request: () => Promise<void>) => {
      try { await request(); }
      catch (cause) { if (!signal.aborted) errors.push(describeError(cause)); }
    };
    const observedActive = new Set<string>();
    const taskPage = async (offset: number, status?: 'queued' | 'running') => {
      const page = await api.listTasks({ service_type: 'image', source_scene: 'shot_image', source_id: shotId, offset, limit: PAGE_SIZE, ...(status ? { status } : {}) }, signal);
      if (signal.aborted) return undefined;
      for (const task of page.items.filter(validTask)) {
        if (status && active(task)) observedActive.add(task.generation_id);
      }
      if (!status) updateCursor(taskCursor, page.items.map(taskId), page.total, refreshing);
      mergeTasks(page.items);
      return page;
    };
    const candidatePage = async (offset: number) => {
      const page = await api.listCandidates({ media_type: 'image', source_scene: 'shot_image', source_id: shotId, offset, limit: PAGE_SIZE }, signal);
      if (signal.aborted) return;
      updateCursor(candidateCursor, page.items.map(assetId), page.total, refreshing && offset === 0);
      loadedCandidateEnd = Math.max(loadedCandidateEnd, offset + page.items.length);
      mergeCandidates(page.items);
      return page;
    };
    const refreshCandidates = async () => {
      const loadedEnd = Math.max(loadedCandidateEnd, candidateCursor.offset);
      const first = await candidatePage(0);
      if (!first || operation === 'poll' || loadedEnd <= PAGE_SIZE) return;
      const refreshEnd = Math.max(loadedEnd, candidateCursor.offset);
      candidateCursor.offset = first.items.length;
      publish({});
      while (!signal.aborted && candidateCursor.offset < Math.min(refreshEnd, candidateCursor.total)) {
        const page = await candidatePage(candidateCursor.offset);
        if (!page || !page.items.length) break;
      }
    };
    const activePages = async (status: 'queued' | 'running') => {
      let offset = 0;
      while (!signal.aborted) {
        const page = await taskPage(offset, status);
        if (!page || !page.items.length) break;
        offset += page.items.length;
        if (offset >= page.total) break;
      }
    };

    if (refreshing) {
      const previousActive = state.tasks.filter(active);
      await Promise.all([
        attempt(async () => { await taskPage(0); }),
        attempt(() => activePages('queued')),
        attempt(() => activePages('running')),
        attempt(refreshCandidates),
      ]);
      for (const task of previousActive) {
        if (signal.aborted) return;
        if (observedActive.has(task.generation_id)) continue;
        await attempt(async () => {
          const detail = await api.detail(task.generation_id, signal);
          if (signal.aborted) return;
          if (detail.generation_id !== task.generation_id || !validTask(detail)) throw new Error('任务来源与当前分镜不符。');
          mergeTasks([detail]);
          mergeCandidates(detail.result.assets);
        });
      }
    } else if (operation === 'tasks' && taskCursor.offset < taskCursor.total) {
      await attempt(async () => { await taskPage(taskCursor.offset); });
    } else if (operation === 'candidates' && candidateCursor.offset < candidateCursor.total) {
      await attempt(async () => { await candidatePage(candidateCursor.offset); });
    }
    if (!signal.aborted) publish({ error: errors.join('；') });
  }

  function enqueue(operation: Operation): Promise<void> {
    if (!controller || controller.signal.aborted) return Promise.resolve();
    const existing = pending.get(operation);
    if (existing) return existing;
    const signal = controller.signal;
    clearTimer();
    pendingCount += 1;
    const request = tail.then(async () => {
      if (signal.aborted) return;
      if (operation === 'refresh' || operation === 'poll') pending.delete(operation);
      publish({ loading: true });
      await execute(operation, signal);
    }).finally(() => {
      if (signal.aborted) return;
      if (pending.get(operation) === request) pending.delete(operation);
      pendingCount -= 1;
      if (pendingCount === 0) {
        publish({ loading: false });
        timer = schedule(() => { timer = undefined; void enqueue('poll'); }, state.tasks.some(active) ? 3000 : 15000);
      }
    });
    pending.set(operation, request);
    tail = request;
    return request;
  }

  return {
    start() {
      if (!controller || controller.signal.aborted) controller = new AbortController();
      return enqueue('refresh');
    },
    stop() {
      clearTimer();
      controller?.abort();
      pending.clear();
      pendingCount = 0;
      tail = Promise.resolve();
      if (state.loading) publish({ loading: false });
    },
    refresh: () => enqueue('refresh'),
    loadMoreTasks: () => enqueue('tasks'),
    loadMoreCandidates: () => enqueue('candidates'),
  };
}
