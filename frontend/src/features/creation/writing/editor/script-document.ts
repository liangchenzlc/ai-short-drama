import type { ScriptScene } from '@host/types/creation/writing/editor/script-document';
export function scriptScenes(content: string): ScriptScene[] {
  const scenes: ScriptScene[] = [];
  let index = 0;
  for (const line of content.split('\n')) {
    const label = line.trim();
    if (
      /^(?:第[零〇一二三四五六七八九十百千\d]+[场幕]|场[次景]\s*[零〇一二三四五六七八九十百千\d]+|\d+\s*[-—.]\s*\d+|(?:内|外|INT|EXT)[.．/／\s])/i.test(
        label,
      )
    ) {
      scenes.push({ label, index });
    }
    index += line.length + 1;
  }
  return scenes;
}

export function scriptWordCount(content: string): number {
  return Array.from(content.replace(/\s/g, '')).length;
}
