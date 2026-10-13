import type { EditorDocument, EditorAttributes } from '../../api/modules/episode-writing';

const attributeNames = new Set(['bold', 'italic', 'header', 'blockquote']);

/** A Quill document contains one final newline in addition to the user's text. */
export function documentText(document: EditorDocument): string {
  const text = document.ops.map(op => op.insert).join('');
  return text.endsWith('\n') ? text.slice(0, -1) : text;
}

export function textDocument(content: string): EditorDocument {
  return { ops: [{ insert: `${content}\n` }] };
}

export function isEditorDocument(value: unknown): value is EditorDocument {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  if (Object.keys(value).some(key => key !== 'ops')) return false;
  const ops = (value as { ops?: unknown }).ops;
  if (!Array.isArray(ops) || !ops.length) return false;
  for (const op of ops) {
    if (!op || typeof op !== 'object' || Array.isArray(op)) return false;
    if (Object.keys(op).some(key => key !== 'insert' && key !== 'attributes')) return false;
    if (typeof op.insert !== 'string' || !op.insert.length) return false;
    if (op.attributes !== undefined) {
      if (!op.attributes || typeof op.attributes !== 'object' || Array.isArray(op.attributes)) return false;
      for (const [key, attribute] of Object.entries(op.attributes)) {
        if (!attributeNames.has(key)) return false;
        if (key === 'header' ? ![1, 2, 3].includes(attribute as number) : typeof attribute !== 'boolean') return false;
      }
    }
  }
  return (ops.at(-1) as { insert: string }).insert.endsWith('\n');
}

/** Canonicalize only supported formats; never persist HTML or media embeds. */
export function normalizeDocument(document: EditorDocument): EditorDocument {
  const ops: EditorDocument['ops'] = [];
  for (const op of document.ops) {
    if (!op.insert) continue;
    const attributes: EditorAttributes = {};
    if (op.attributes?.bold) attributes.bold = true;
    if (op.attributes?.italic) attributes.italic = true;
    if (op.attributes?.header) attributes.header = op.attributes.header;
    if (op.attributes?.blockquote) attributes.blockquote = true;
    const present = Object.keys(attributes).length > 0;
    const last = ops.at(-1);
    if (last && JSON.stringify(last.attributes ?? {}) === JSON.stringify(attributes)) last.insert += op.insert;
    else ops.push({ insert: op.insert, ...(present ? { attributes } : {}) });
  }
  return { ops };
}

export function sameDocument(a: EditorDocument | null | undefined, b: EditorDocument | null | undefined, content: string): boolean {
  return JSON.stringify(normalizeDocument(a ?? textDocument(content))) === JSON.stringify(normalizeDocument(b ?? textDocument(content)));
}

export interface ScriptScene { label: string; index: number }
export function scriptScenes(content: string): ScriptScene[] {
  const scenes: ScriptScene[] = [];
  let index = 0;
  for (const line of content.split('\n')) {
    const label = line.trim();
    if (/^(?:第[零〇一二三四五六七八九十百千\d]+[场幕]|场[次景]\s*[零〇一二三四五六七八九十百千\d]+|\d+\s*[-—.]\s*\d+|(?:内|外|INT|EXT)[.．/／\s])/i.test(label)) {
      scenes.push({ label, index });
    }
    index += line.length + 1;
  }
  return scenes;
}

export function scriptWordCount(content: string): number {
  return Array.from(content.replace(/\s/g, '')).length;
}
