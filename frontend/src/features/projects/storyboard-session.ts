export function mergeStoryboardReload<T extends { items: { id: string }[] }>(remote: T, local: T | null, unsettledIds: ReadonlySet<string>): T {
  if (!local || unsettledIds.size === 0) return remote;
  const localById = new Map(local.items.map((item) => [item.id, item]));
  const remoteIds = new Set(remote.items.map((item) => item.id));
  const items = remote.items.map((item) => unsettledIds.has(item.id) ? (localById.get(item.id) ?? item) : item);
  for (const item of local.items) if (unsettledIds.has(item.id) && !remoteIds.has(item.id)) items.push(item);
  return { ...remote, items };
}

export function hasUnsettledStoryboard(dirty: ReadonlySet<string>, saving: ReadonlyMap<string, unknown>, timers: ReadonlyMap<string, unknown>) {
  return dirty.size > 0 || saving.size > 0 || timers.size > 0;
}

export function shouldPollStoryboardTasks(tasks: readonly { status: string }[]) {
  return tasks.some((task) => task.status === 'queued' || task.status === 'running');
}
