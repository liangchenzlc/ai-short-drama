import type { AgentSkill, AgentSkillContent, AgentSkillDetail, AgentSkillResource } from '../../api/types/agents';

export type SkillResourceDraft = Pick<AgentSkillResource, 'path' | 'kind'> & {
  content: string | null; origin_version?: string; checksum_sha256?: string;
};
export type SkillFormValues = Required<Omit<AgentSkillContent, 'resources'>> & { resources: SkillResourceDraft[] };
export interface SkillSubmission { key: string; body: AgentSkillContent; row_version: string | null }
export interface SkillDraft {
  schema_version: 1; key: string; base: AgentSkillDetail | null; values: SkillFormValues;
  changed: boolean; pending: SkillSubmission | null; conflict: boolean;
}
export type SkillPendingAction =
  | { kind: 'copy'; item: AgentSkill; key: string }
  | { kind: 'settings'; item: AgentSkill; enabled: boolean; invocation_mode: 'auto' | 'manual' }
  | { kind: 'delete'; item: AgentSkill };
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const draftKey = (account: string) => `agent-skill-draft:v1:${encodeURIComponent(account)}`;
const actionKey = (account: string) => `agent-skill-action:v1:${encodeURIComponent(account)}`;
const bytes = (value: string) => new TextEncoder().encode(value).length;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string');
const positive = (value: unknown) => typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value);

export function createSkillDraft(detail?: AgentSkillDetail, imported?: AgentSkillContent): SkillDraft {
  const key = crypto.randomUUID();
  return {
    schema_version: 1, key, base: detail ?? null, changed: !!imported, pending: null, conflict: false,
    values: {
      name: detail?.name ?? '', slug: detail?.slug ?? `skill-${key.slice(0, 8)}`,
      description: detail?.description ?? '', instructions: detail?.instructions ?? '',
      tags: detail?.tags ?? [], modes: detail?.modes ?? ['standard', 'infinite_canvas'],
      exclusions: detail?.exclusions ?? '', required_tools: detail?.required_tools ?? [], source: detail?.source ?? '',
      enabled: detail?.enabled ?? true, invocation_mode: detail?.invocation_mode ?? 'auto',
      ...imported,
      resources: imported?.resources?.map(item => ({ ...item }))
        ?? detail?.resources.map(item => ({ path: item.path, kind: item.kind, content: null,
          origin_version: detail.content_version, checksum_sha256: item.checksum_sha256 })) ?? [],
    },
  };
}

export function skillResourcePathIssue(path: string): string {
  const parts = path.split('/');
  return !path || bytes(path) > 255 || parts.length > 6
    || parts.some(part => !part || part === '.' || part === '..' || /[. ]$/.test(part))
    || /[\u0000-\u001f\\:%?#]/.test(path) || /^skill\.md$/i.test(path)
    || !/\.(md|txt|json|csv|ya?ml)$/i.test(path)
    ? '资料需要相对文本路径，例如 references/notes.md；最多六层目录。' : '';
}

export function skillDraftIssue(values: SkillFormValues): string {
  if (!values.name.trim() || !values.description.trim() || !values.instructions.trim()) return '请填写名称、用途和主指令。';
  if (values.name.trim().length > 120 || values.description.trim().length > 2000) return '名称最多 120 字，用途最多 2000 字。';
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(values.slug) || values.slug.length > 64) return '格式标识只使用小写字母、数字和连字符，最多 64 字符。';
  if (!values.modes.length) return '至少选择一种适用模式。';
  if (values.instructions.includes('\0') || bytes(values.instructions.trim()) > 65536) return '主指令最多 64 KiB，不能包含 NUL 字符。';
  if (values.resources.length > 31) return '最多添加 31 份资料与模板。';
  const paths = new Set<string>();
  let size = bytes(values.instructions.trim());
  for (const resource of values.resources) {
    const issue = skillResourcePathIssue(resource.path);
    if (issue) return issue;
    const path = resource.path.normalize('NFC').toLowerCase();
    if (paths.has(path)) return '资料路径不能重复，大小写不同也视为相同路径。';
    paths.add(path);
    if (resource.content !== null) {
      const length = bytes(resource.content); size += length;
      if (length > 131072 || resource.content.includes('\0')) return `资料 ${resource.path} 超过 128 KiB 或包含 NUL 字符。`;
    }
  }
  if (size > 1048576) return '主指令和资料合计不能超过 1 MiB。';
  if (values.tags.length > 16 || values.tags.some(tag => !tag.trim() || tag.length > 64)) return '最多 16 个标签，每个最多 64 字。';
  if (values.required_tools.length > 24 || values.required_tools.some(tool => !/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(tool))) return '请核对工具声明，每行填写一个平台工具名称。';
  return '';
}

export function skillContent(values: SkillFormValues): AgentSkillContent {
  const issue = skillDraftIssue(values);
  if (issue) throw new Error(issue);
  if (values.resources.some(item => item.content === null)) throw new Error('部分原资料尚未读取，不能发布不完整版本。');
  return { ...values, name: values.name.trim(), description: values.description.trim(), instructions: values.instructions.trim(),
    resources: values.resources.map(item => ({ path: item.path.normalize('NFC'), kind: item.kind, content: item.content! })) };
}

function validValues(value: unknown, allowUnloaded: boolean): value is SkillFormValues {
  if (!record(value) || !['name', 'slug', 'description', 'instructions', 'exclusions', 'source'].every(key => typeof value[key] === 'string')
    || typeof value.enabled !== 'boolean' || !['auto', 'manual'].includes(String(value.invocation_mode))
    || !strings(value.tags) || !strings(value.modes) || value.modes.some(mode => !['standard', 'infinite_canvas'].includes(mode))
    || !strings(value.required_tools) || !Array.isArray(value.resources) || value.resources.length > 31) return false;
  return value.resources.every(item => record(item) && typeof item.path === 'string'
    && ['reference', 'template'].includes(String(item.kind))
    && (typeof item.content === 'string' || allowUnloaded && item.content === null && positive(item.origin_version)
      && typeof item.checksum_sha256 === 'string' && /^[a-f0-9]{64}$/.test(item.checksum_sha256)));
}
function validItem(value: unknown): value is AgentSkill {
  return record(value) && typeof value.id === 'string' && typeof value.name === 'string' && typeof value.builtin === 'boolean'
    && positive(value.content_version) && (value.row_version === null || positive(value.row_version))
    && typeof value.settings_version === 'string' && /^\d{1,20}$/.test(value.settings_version)
    && typeof value.enabled === 'boolean' && ['auto', 'manual'].includes(String(value.invocation_mode))
    && ['slug', 'description', 'exclusions', 'source', 'checksum_sha256', 'package_checksum'].every(key => typeof value[key] === 'string')
    && strings(value.tags) && strings(value.modes) && value.modes.every(mode => ['standard', 'infinite_canvas'].includes(mode))
    && strings(value.required_tools) && strings(value.issues) && typeof value.compatible === 'boolean'
    && typeof value.resource_count === 'number' && Number.isSafeInteger(value.resource_count) && value.resource_count >= 0
    && (value.updated_at === null || typeof value.updated_at === 'string')
    && (value.filename === null || typeof value.filename === 'string');
}
function validDraft(value: unknown): value is SkillDraft {
  if (!record(value) || value.schema_version !== 1 || typeof value.key !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(value.key)
    || typeof value.changed !== 'boolean' || typeof value.conflict !== 'boolean' || !validValues(value.values, true)
    || value.base !== null && (!record(value.base) || typeof value.base.instructions !== 'string' || !Array.isArray(value.base.resources) || !validItem(value.base))) return false;
  if (value.pending === null) return true;
  const pending = value.pending;
  return record(pending) && pending.key === value.key && validValues(pending.body, false)
    && (pending.row_version === null && value.base === null
      || record(value.base) && pending.row_version === value.base.row_version && positive(pending.row_version));
}
export function readSkillDraft(account: string, storage: Store): { draft: SkillDraft | null; error: string } {
  try {
    const raw = storage.getItem(draftKey(account));
    if (!raw) return { draft: null, error: '' };
    if (bytes(raw) <= 3 * 1048576) {
      const value: unknown = JSON.parse(raw);
      if (validDraft(value)) return { draft: value, error: '' };
    }
  } catch { /* Preserve the original record for explicit recovery. */ }
  return { draft: null, error: 'Skill 草稿无法读取，原记录已保留。请下载或明确清除后再编辑。' };
}
export function saveSkillDraft(account: string, draft: SkillDraft, storage: Store, settled = false): boolean {
  if (!validDraft(draft)) return false;
  const prior = readSkillDraft(account, storage);
  if (prior.error || !settled && prior.draft?.pending && JSON.stringify(prior.draft.pending) !== JSON.stringify(draft.pending)) return false;
  try {
    const raw = JSON.stringify(draft);
    if (bytes(raw) > 3 * 1048576) return false;
    storage.setItem(draftKey(account), raw); return true;
  } catch { return false; }
}
export function clearSkillDraft(account: string, storage: Store): boolean {
  try { storage.removeItem(draftKey(account)); return true; } catch { return false; }
}
export function rawSkillDraft(account: string, storage: Store): string {
  return storage.getItem(draftKey(account)) ?? '';
}

function validAction(value: unknown): value is SkillPendingAction {
  return record(value) && validItem(value.item) && (value.kind === 'delete'
    || value.kind === 'copy' && typeof value.key === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(value.key)
    || value.kind === 'settings' && typeof value.enabled === 'boolean' && ['auto', 'manual'].includes(String(value.invocation_mode)));
}
export function readSkillAction(account: string, storage: Store): { action: SkillPendingAction | null; error: string } {
  try {
    const raw = storage.getItem(actionKey(account));
    if (!raw) return { action: null, error: '' };
    if (bytes(raw) < 65536) {
      const value: unknown = JSON.parse(raw);
      if (validAction(value)) return { action: value, error: '' };
    }
  } catch { /* Do not replace a request whose acceptance is unknown. */ }
  return { action: null, error: 'Skill 操作记录无法读取。原记录已保留，请下载核对后明确清除。' };
}
export function saveSkillAction(account: string, action: SkillPendingAction | null, storage: Store): boolean {
  if (action && !validAction(action)) return false;
  try {
    if (action) {
      const { instructions: _instructions, resources: _resources, ...item } = action.item as AgentSkillDetail;
      storage.setItem(actionKey(account), JSON.stringify({ ...action, item }));
    } else storage.removeItem(actionKey(account));
    return true;
  } catch { return false; }
}
export function rawSkillAction(account: string, storage: Store): string { return storage.getItem(actionKey(account)) ?? ''; }

export function downloadSkillText(name: string, content: string | Blob) {
  const url = URL.createObjectURL(content instanceof Blob ? content : new Blob([content], { type: 'application/json;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
