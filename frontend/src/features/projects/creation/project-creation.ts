import type { CreationStorage, ProjectCreationAttempt } from '@host/types/projects/creation/project-creation';
import type { ProjectCreateFields } from '@host/types/projects/management';
const storageKey = (scope: string) => `short-drama:project-create-v2:${scope}`;
const fingerprint = (body: ProjectCreateFields) => JSON.stringify([body.workspace_mode, body.name]);

export function readProjectCreation(storage: CreationStorage, scope: string): ProjectCreationAttempt | null {
  const raw = storage.getItem(storageKey(scope));
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as ProjectCreationAttempt;
    if (
      !value ||
      typeof value.key !== 'string' ||
      !/^[\w-]{1,128}$/.test(value.key) ||
      !['standard', 'infinite_canvas'].includes(value.body?.workspace_mode) ||
      typeof value.body.name !== 'string' ||
      value.fingerprint !== fingerprint(value.body)
    )
      return null;
    return value;
  } catch {
    return null;
  }
}

export function prepareProjectCreation(
  storage: CreationStorage,
  scope: string,
  body: ProjectCreateFields,
  newKey: () => string,
): ProjectCreationAttempt {
  const previous = readProjectCreation(storage, scope);
  if (previous?.fingerprint === fingerprint(body)) return previous;
  const attempt = { key: newKey(), body, fingerprint: fingerprint(body) };
  storage.setItem(storageKey(scope), JSON.stringify(attempt));
  return attempt;
}

export function clearProjectCreation(storage: CreationStorage, scope: string, key: string) {
  if (readProjectCreation(storage, scope)?.key === key) storage.removeItem(storageKey(scope));
}
