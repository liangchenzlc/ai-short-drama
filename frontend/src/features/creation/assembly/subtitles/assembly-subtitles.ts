import type { Subtitle } from '@host/types/creation/sound/tracks';
export function validateSubtitles(items: Subtitle[]) {
  if (items.length > 2000) throw new Error('字幕最多支持 2000 条');
  const sorted = [...items].sort((a, b) => a.start_ms - b.start_ms);
  sorted.forEach((s, index) => {
    if (
      !s.text.trim() ||
      s.text.length > 1000 ||
      !Number.isSafeInteger(s.start_ms) ||
      !Number.isSafeInteger(s.end_ms) ||
      s.start_ms < 0 ||
      s.end_ms <= s.start_ms ||
      s.end_ms > 3600000 ||
      (index > 0 && sorted[index - 1].end_ms > s.start_ms)
    )
      throw new Error('字幕内容不能为空，时间须在一小时内且不能重叠');
  });
  return sorted;
}
export function parseSrt(text: string): Subtitle[] {
  const stamp = (a: string, b: string, c: string, d: string) => ((+a * 60 + +b) * 60 + +c) * 1000 + +d;
  const items = text
    .replace(/^\uFEFF/, '')
    .replace(/\r/g, '')
    .trim()
    .split(/\n\s*\n/)
    .map((block) => {
      const lines = block.split('\n');
      if (/^\d+$/.test(lines[0])) lines.shift();
      const match = lines
        .shift()
        ?.match(/^(\d{2}):([0-5]\d):([0-5]\d)[,.](\d{3})\s*-->\s*(\d{2}):([0-5]\d):([0-5]\d)[,.](\d{3})\s*$/);
      if (!match) throw new Error('SRT 时间格式无效，请使用 UTF-8 字幕文件');
      return {
        id: crypto.randomUUID(),
        start_ms: stamp(match[1], match[2], match[3], match[4]),
        end_ms: stamp(match[5], match[6], match[7], match[8]),
        text: lines.join('\n').trim(),
      };
    });
  return validateSubtitles(items);
}
export function exportSrt(items: Subtitle[]) {
  const stamp = (ms: number) =>
    `${Math.floor(ms / 3600000)
      .toString()
      .padStart(2, '0')}:${Math.floor((ms / 60000) % 60)
      .toString()
      .padStart(2, '0')}:${Math.floor((ms / 1000) % 60)
      .toString()
      .padStart(2, '0')},${Math.round(ms % 1000)
      .toString()
      .padStart(3, '0')}`;
  return (
    validateSubtitles(items)
      .map((s, i) => `${i + 1}\n${stamp(s.start_ms)} --> ${stamp(s.end_ms)}\n${s.text}`)
      .join('\n\n') + '\n'
  );
}
export function splitSubtitle(items: Subtitle[], id: string, at: number, offset?: number): Subtitle[] {
  return items.flatMap((s) => {
    if (s.id !== id || at <= s.start_ms || at >= s.end_ms) return [s];
    const cut =
      offset ??
      Math.max(
        1,
        Math.min(s.text.length - 1, Math.round((s.text.length * (at - s.start_ms)) / (s.end_ms - s.start_ms))),
      );
    if (cut <= 0 || cut >= s.text.length) return [s];
    return [
      { ...s, end_ms: at, text: s.text.slice(0, cut) },
      {
        ...s,
        id: crypto.randomUUID(),
        start_ms: at,
        text: s.text.slice(cut),
        anchor_source_in_ms: s.anchor_source_in_ms == null ? null : s.anchor_source_in_ms + at - s.start_ms,
      },
    ];
  });
}
export function downloadText(text: string, name: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
