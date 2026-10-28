import type { AgentSkill, AgentSkillContent, AgentSkillDetail } from '../../api/types/agents';

export type SkillModeChoice = 'general' | 'standard' | 'infinite_canvas';
export const modeOptions = [
  { value: 'general', label: '通用' },
  { value: 'standard', label: '创作' },
  { value: 'infinite_canvas', label: '画布' },
];
export function modeChoice(modes: AgentSkill['modes']): SkillModeChoice {
  return modes.length === 1 ? modes[0] : 'general';
}
export function modeValues(choice: SkillModeChoice): AgentSkill['modes'] {
  return choice === 'general' ? ['standard', 'infinite_canvas'] : [choice];
}
export function modeLabel(modes: AgentSkill['modes']): string {
  return modeOptions.find(item => item.value === modeChoice(modes))!.label;
}

export function instructionBody(content: string): string {
  // Old Markdown uploads may have stored the header as part of the instructions.
  const header = content.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  return (header && /^(?:name|description|metadata):/m.test(header[1]) ? content.slice(header[0].length) : content).trim();
}

export interface SkillFileNode {
  key: string; title: string; isLeaf: boolean; children?: SkillFileNode[];
}
export function skillFileTree(paths: string[]): SkillFileNode[] {
  const roots: SkillFileNode[] = [];
  for (const path of paths) {
    let siblings = roots;
    const parts = path.split('/');
    for (let index = 0; index < parts.length; index++) {
      const key = parts.slice(0, index + 1).join('/');
      let node = siblings.find(item => item.key === key);
      if (!node) {
        node = { key, title: parts[index], isLeaf: index === parts.length - 1 };
        if (!node.isLeaf) node.children = [];
        siblings.push(node);
      }
      siblings = node.children ?? [];
    }
  }
  function sort(nodes: SkillFileNode[]): SkillFileNode[] {
    return nodes.sort((a, b) => a.key === 'SKILL.md' ? -1 : b.key === 'SKILL.md' ? 1
      : Number(a.isLeaf) - Number(b.isLeaf) || a.title.localeCompare(b.title, 'zh-CN'))
      .map(node => ({ ...node, ...(node.children ? { children: sort(node.children) } : {}) }));
  }
  return sort(roots);
}

export async function resourceChecksum(content: string, encoding?: string): Promise<string> {
  const data = encoding === 'base64' ? Uint8Array.from(atob(content), character => character.charCodeAt(0)) : new TextEncoder().encode(content);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}
export async function matchesSubmission(detail: AgentSkillDetail, body: AgentSkillContent): Promise<boolean> {
  const fields = ['name', 'slug', 'description', 'instructions', 'tags', 'modes', 'exclusions', 'required_tools', 'source'] as const;
  if (fields.some(field => body[field] !== undefined && JSON.stringify(detail[field]) !== JSON.stringify(body[field]))) return false;
  if (detail.resources.length !== body.resources?.length) return false;
  for (const resource of body.resources ?? []) {
    const checksum = await resourceChecksum(resource.content, resource.encoding);
    if (!detail.resources.some(item => item.path === resource.path && item.kind === resource.kind && item.checksum_sha256 === checksum)) return false;
  }
  return true;
}

export async function packageChanges(base: AgentSkillDetail, body: AgentSkillContent) {
  const changed: string[] = [];
  const added: string[] = [];
  const resources = body.resources ?? [];
  for (const item of resources) {
    const original = base.resources.find(resource => resource.path === item.path);
    if (!original) added.push(item.path);
    else if (original.kind !== item.kind || original.checksum_sha256 !== await resourceChecksum(item.content, item.encoding)) changed.push(item.path);
  }
  const fields = ['name', 'slug', 'description', 'instructions', 'tags', 'modes', 'exclusions', 'required_tools', 'source'] as const;
  if (fields.some(field => body[field] !== undefined && JSON.stringify(base[field]) !== JSON.stringify(body[field]))) changed.unshift('SKILL.md');
  const removed = base.resources.filter(item => !resources.some(resource => resource.path === item.path)).map(item => item.path);
  return { added, changed, removed };
}
