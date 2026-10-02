type AccountStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function workflowStorage(): AccountStore {
  try { return window.localStorage; } catch {
    return { getItem: () => null, setItem: () => { throw new Error('Browser storage is unavailable'); }, removeItem: () => {} };
  }
}

export function accountStorage(accountId: string, storage: AccountStore = workflowStorage()): AccountStore {
  const prefix = `short-drama:user:${accountId}:`;
  return {
    getItem: key => storage.getItem(prefix + key),
    setItem: (key, value) => storage.setItem(prefix + key, value),
    removeItem: key => storage.removeItem(prefix + key),
  };
}
