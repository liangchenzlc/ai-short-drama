/** Standard-workspace queues outlive the page that subscribes to them. */
export interface StandardSessionController {
  getSnapshot(): { status?: string; message?: string; dirty?: boolean; busy?: boolean; recoveryBlocked?: boolean };
  subscribe(listener: () => void): () => unknown;
  dispose(): void;
  suspend?(): void;
  resume?(): void;
  hasUnsettled?(): boolean;
  hasUnpersisted?(): boolean;
  hasMemoryOnlyChanges?(): boolean;
  flush?(): Promise<unknown>;
  retry?(): Promise<unknown>;
}
export interface StandardSessionDetails { label?: string; projectId?: string }
export interface StandardSaveEntry extends StandardSessionDetails {
  key: string; status: string; message: string; hasUnpersisted: boolean;
  flush?: () => Promise<unknown>; retry?: () => Promise<unknown>;
}
interface Entry {
  accountId: string; resourceKey: string; controller: StandardSessionController;
  details: StandardSessionDetails; references: number; unsubscribe: () => unknown;
  timer?: ReturnType<typeof setTimeout>;
}
const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();
let account: string | null = null;
let accountInitialized = false;
let snapshot: readonly StandardSaveEntry[] = [];
let snapshotAccount: string | null = null;
let notifyPending = false;
const entryKey = (actor: string, resource: string) => JSON.stringify([actor, resource]);
function publish() {
  const next = [...entries.values()].filter(entry => entry.accountId === account).map(entry => {
    const state = entry.controller.getSnapshot();
    return { ...entry.details, key: entry.resourceKey, status: state.recoveryBlocked ? 'conflict' : state.status === 'loading' && !state.busy && !state.dirty ? 'idle' : state.status === 'dirty' ? 'unsaved' : state.status ?? (state.dirty ? 'unsaved' : 'saved'),
      message: state.message ?? '', hasUnpersisted: entry.controller.hasMemoryOnlyChanges?.() ?? entry.controller.hasUnpersisted?.() ?? false,
      flush: entry.controller.flush ? () => entry.controller.flush!() : undefined,
      retry: entry.controller.retry ? () => entry.controller.retry!() : undefined };
  });
  if (snapshotAccount === account && next.length === snapshot.length && next.every((entry, index) => {
    const previous = snapshot[index];
    return entry.key === previous.key && entry.status === previous.status && entry.message === previous.message
      && entry.label === previous.label && entry.projectId === previous.projectId
      && entry.hasUnpersisted === previous.hasUnpersisted;
  })) return;
  snapshot = next; snapshotAccount = account;
  if (!notifyPending) {
    notifyPending = true;
    queueMicrotask(() => { notifyPending = false; listeners.forEach(listener => listener()); });
  }
}
function scheduleCleanup(key: string, entry: Entry) {
  if (entries.get(key) !== entry || entry.references || entry.timer || entry.controller.hasUnsettled?.()) return;
  entry.timer = setTimeout(() => {
    entry.timer = undefined;
    if (entry.references || entry.controller.hasUnsettled?.()) return;
    entry.controller.dispose(); entry.unsubscribe(); entries.delete(key); publish();
  }, 30_000);
}
export const getStandardSaveSnapshot = () => snapshot;
export const subscribeStandardSaveState = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export function setStandardSessionAccount(accountId: string | null) {
  accountInitialized = true;
  if (account === accountId) return;
  account = accountId;
  for (const [key, entry] of entries) {
    if (entry.accountId === account) { entry.controller.resume?.(); continue; }
    clearTimeout(entry.timer); entry.timer = undefined;
    if (entry.controller.suspend) entry.controller.suspend();
    else { entry.controller.dispose(); entry.unsubscribe(); entries.delete(key); }
    scheduleCleanup(key, entry);
  }
  publish();
}
export function retainStandardSession<T extends StandardSessionController>(accountId: string, resourceKey: string, create: () => T, details: StandardSessionDetails = {}): T {
  const key = entryKey(accountId, resourceKey);
  const existing = entries.get(key);
  if (existing) {
    clearTimeout(existing.timer); existing.timer = undefined; existing.references += 1;
    const nextDetails = { ...existing.details, ...details };
    const detailsChanged = existing.details.label !== nextDetails.label
      || existing.details.projectId !== nextDetails.projectId;
    existing.details = nextDetails;
    if (account === accountId) existing.controller.resume?.();
    if (detailsChanged) publish();
    return existing.controller as T;
  }
  const controller = create();
  const entry: Entry = { accountId, resourceKey, controller, details, references: 1, unsubscribe: () => {} };
  entries.set(key, entry);
  entry.unsubscribe = controller.subscribe(() => { publish(); scheduleCleanup(key, entry); });
  // The authenticated application normally sets this before mounting its routes.
  if (!accountInitialized) { account = accountId; accountInitialized = true; }
  if (account !== accountId) controller.suspend?.();
  publish();
  return controller;
}
export function releaseStandardSession(accountId: string, resourceKey: string) {
  const key = entryKey(accountId, resourceKey);
  const entry = entries.get(key);
  if (!entry) return;
  entry.references = Math.max(0, entry.references - 1);
  if (entry.references) return;
  // Do not await the network to release a page. Dirty and failed queues stay owned here.
  if (entry.accountId === account && entry.controller.hasUnsettled?.()) {
    const pending = entry.controller.flush?.();
    if (pending) void pending.then(() => scheduleCleanup(key, entry), () => scheduleCleanup(key, entry));
  }
  scheduleCleanup(key, entry);
}
export function hasStandardUnpersistedChanges() {
  return [...entries.values()].some(entry => entry.accountId === account && (entry.controller.hasMemoryOnlyChanges?.() ?? entry.controller.hasUnpersisted?.()));
}
export function retryStandardSessions() {
  return Promise.allSettled([...entries.values()].filter(entry => entry.accountId === account)
    .map(entry => entry.controller.retry?.() ?? entry.controller.flush?.()));
}
export async function flushStandardProjectSessions(projectId: string): Promise<boolean> {
  const selected = [...entries.values()].filter(entry => entry.accountId === account && entry.details.projectId === projectId);
  for (const entry of selected) {
    if (entry.controller.hasUnsettled?.()) await entry.controller.flush?.();
    if (entry.controller.hasUnsettled?.()) return false;
  }
  return true;
}
