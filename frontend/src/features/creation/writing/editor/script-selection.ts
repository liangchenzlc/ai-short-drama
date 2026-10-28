import type { ScriptSelection } from '@host/types/creation/writing/rewrite/writing-requests';
export function scriptCharacterCount(text: string) {
  let count = 0;
  for (const character of text) if (!/\s/u.test(character)) ++count;
  return count;
}

export function scriptExportName(title: string) {
  const name = title
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '')
    .replace(/[. ]+$/g, '')
    .trim();
  return `${name || '作品'}-剧本.txt`;
}

export function selectionSummary(text: string) {
  const points = Array.from(text);
  return points.length <= 90 ? text : `${points.slice(0, 44).join('')}…${points.slice(-44).join('')}`;
}

function textPosition(root: HTMLElement, position: number) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let remaining = position;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const length = node.textContent?.length ?? 0;
    if (remaining <= length) return { node, offset: remaining };
    remaining -= length;
  }
  return null;
}

/** Measure rendered text so wrapping, zoom and long selections share one geometry. */
export function captureScriptSelection(
  input: HTMLTextAreaElement,
  display: HTMLElement,
  scroll: HTMLElement,
): ScriptSelection | null {
  const start = input.selectionStart,
    end = input.selectionEnd;
  const text = input.value.slice(start, end);
  if (!text.trim() || !display.getClientRects().length) return null;
  const from = textPosition(display, start),
    to = textPosition(display, end);
  if (!from || !to) return null;
  const range = document.createRange();
  range.setStart(from.node, from.offset);
  range.setEnd(to.node, to.offset);
  const host = scroll.getBoundingClientRect();
  const visible = Array.from(range.getClientRects()).filter(
    (rect) => rect.height > 0 && rect.bottom > host.top && rect.top < host.bottom,
  );
  const anchor = input.selectionDirection === 'backward' ? visible[0] : visible.at(-1);
  const left = anchor?.left ?? host.left + 20;
  const top = Math.max(host.top + 8, anchor?.top ?? host.top + 8);
  const bottom = Math.min(host.bottom - 8, anchor?.bottom ?? top + 30);
  return { start, end, text, source: input.value, left, top, bottom };
}
