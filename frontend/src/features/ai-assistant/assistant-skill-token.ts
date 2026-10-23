import type { AssistantSkillChoice } from './AssistantSkillPicker';

/** 选中的方法在正文里以 /名称 出现，这份文本与 skills 列表必须始终一致。 */
export function skillToken(skill: Pick<AssistantSkillChoice, 'id' | 'name'>) {
  return `/${skill.name?.trim() || skill.id}`;
}

export function appendSkillToken(text: string, token: string) {
  if (!text) return `${token} `;
  return /[\s]$/.test(text) ? `${text}${token} ` : `${text} ${token} `;
}

export function removeSkillToken(text: string, token: string) {
  const index = text.indexOf(token);
  if (index < 0) return text;
  const after = index + token.length;
  // 只吃掉一个分隔空格，保留其余正文的原有间距。
  if (text[after] === ' ') return text.slice(0, index) + text.slice(after + 1);
  if (index > 0 && text[index - 1] === ' ') return text.slice(0, index - 1) + text.slice(after);
  return text.slice(0, index) + text.slice(after);
}

export function ensureSkillTokens(text: string, skills: readonly AssistantSkillChoice[]) {
  return skills.reduce((next, skill) => next.includes(skillToken(skill)) ? next : appendSkillToken(next, skillToken(skill)), text);
}

export function retainTokenizedSkills(text: string, skills: readonly AssistantSkillChoice[]) {
  return skills.filter(skill => text.includes(skillToken(skill)));
}
