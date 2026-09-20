export type ListedMedia = { id: string; mime: string; availability: 'available' | 'missing' };
export type MediaImportResult = { ok: true; mediaId: string } | { ok: false; reason: 'cancelled' | 'failure' | 'uncertain'; message?: string };
type StoredMedia = ListedMedia & { projectId: string; file: Blob };
const urls = new Map<string, string>();
const key = (projectId: string, id: string) => `${projectId}/${id}`;
function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('short-drama-web-media', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('media', { keyPath: 'id' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
export function listUsableMediaResult<T extends ListedMedia>(items: readonly T[], mimePrefix: string): T[] {
  return items.filter((item) => item.availability === 'available' && item.mime.startsWith(mimePrefix));
}
export function hasAvailableMedia(items: readonly ListedMedia[], mediaId: string, mimePrefix: string) {
  return items.some((item) => item.id === mediaId && item.availability === 'available' && (mimePrefix.endsWith('/') ? item.mime.startsWith(mimePrefix) : item.mime === mimePrefix));
}
export async function listUsableMedia(projectId: string, mimePrefix: string): Promise<ListedMedia[]> {
  try {
    const db = await database();
    const items = await new Promise<StoredMedia[]>((resolve, reject) => {
      const request = db.transaction('media', 'readonly').objectStore('media').getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    db.close();
    const matches = listUsableMediaResult(items.filter((item) => item.projectId === projectId), mimePrefix);
    matches.forEach((item) => { if (!urls.has(key(projectId, item.id))) urls.set(key(projectId, item.id), URL.createObjectURL(item.file)); });
    return matches;
  } catch { return []; }
}
export function mediaUrl(projectId: string, mediaId: string) { return urls.get(key(projectId, mediaId)) ?? null; }
export function importProjectMedia(projectId: string, purpose: 'reference' | 'speech' | 'video' | 'music' | 'sfx' | 'evidence'): Promise<MediaImportResult> {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    const prefix = purpose === 'video' ? 'video/' : ['speech', 'music', 'sfx'].includes(purpose) ? 'audio/' : 'image/';
    input.accept = prefix + '*';
    input.oncancel = () => resolve({ ok: false, reason: 'cancelled' });
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) { resolve({ ok: false, reason: 'cancelled' }); return; }
      if (!file.type.startsWith(prefix) || file.size > 100 * 1024 * 1024) { resolve({ ok: false, reason: 'failure', message: '请选择正确类型且小于 100 MB 的媒体文件。' }); return; }
      try {
        const id = crypto.randomUUID();
        const db = await database();
        await new Promise<void>((done, reject) => {
          const transaction = db.transaction('media', 'readwrite');
          transaction.objectStore('media').put({ id, projectId, mime: file.type, availability: 'available', file } satisfies StoredMedia);
          transaction.oncomplete = () => done();
          transaction.onerror = () => reject(transaction.error);
          transaction.onabort = () => reject(transaction.error);
        });
        db.close();
        urls.set(key(projectId, id), URL.createObjectURL(file));
        resolve({ ok: true, mediaId: id });
      } catch { resolve({ ok: false, reason: 'failure', message: '导入失败，请检查浏览器存储权限或可用空间。' }); }
    };
    input.click();
  });
}
