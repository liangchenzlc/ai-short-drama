import type { ProjectCreateFields } from '../../api/modules/projects';

export interface ProjectCreationAttempt { key: string; body: ProjectCreateFields; fingerprint: string }
export interface CreationStorage { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
const storageKey = (scope: string) => `short-drama:canvas-project-create:${scope}`;
const fingerprint = (body: ProjectCreateFields) => JSON.stringify([body.workspace_mode, body.name, body.aspect, body.synopsis, body.style]);

export function readProjectCreation(storage: CreationStorage, scope: string): ProjectCreationAttempt | null {
  const raw = storage.getItem(storageKey(scope));
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as ProjectCreationAttempt;
    if (!value || typeof value.key !== 'string' || !/^[\w-]{1,128}$/.test(value.key)
      || value.body?.workspace_mode !== 'infinite_canvas' || typeof value.body.name !== 'string'
      || !['16:9', '9:16'].includes(value.body.aspect) || value.fingerprint !== fingerprint(value.body)) return null;
    return value;
  } catch { return null; }
}

export function prepareProjectCreation(storage: CreationStorage, scope: string, body: ProjectCreateFields, newKey: () => string): ProjectCreationAttempt {
  const previous = readProjectCreation(storage, scope);
  if (previous?.fingerprint === fingerprint(body)) return previous;
  const attempt = { key: newKey(), body, fingerprint: fingerprint(body) };
  storage.setItem(storageKey(scope), JSON.stringify(attempt));
  return attempt;
}

export function clearProjectCreation(storage: CreationStorage, scope: string, key: string) {
  if (readProjectCreation(storage, scope)?.key === key) storage.removeItem(storageKey(scope));
}
