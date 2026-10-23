export interface TokenRange { start: number; end: number }
export interface HighlightPart { text: string; token: boolean }

/** 正文里所有需要高亮/整词删除的词条区间，重叠时只保留先出现的。 */
export function tokenRanges(value: string, tokens: readonly string[]): TokenRange[] {
  const ranges: TokenRange[] = [];
  tokens.filter(token => token.length > 1).forEach(token => {
    let from = 0;
    while (from <= value.length - token.length) {
      const start = value.indexOf(token, from);
      if (start < 0) break;
      const end = start + token.length;
      if (!ranges.some(range => start < range.end && end > range.start)) ranges.push({ start, end });
      from = end;
    }
  });
  return ranges.sort((left, right) => left.start - right.start);
}

/** 把已选中的 Skill、@ 引用词条从文本里切出来，其余部分保持原样。 */
export function splitHighlightedText(value: string, tokens: readonly string[]): HighlightPart[] {
  const ranges = tokenRanges(value, tokens);
  if (!ranges.length) return value ? [{ text: value, token: false }] : [];
  const parts: HighlightPart[] = [];
  let cursor = 0;
  ranges.forEach(range => {
    if (range.start > cursor) parts.push({ text: value.slice(cursor, range.start), token: false });
    parts.push({ text: value.slice(range.start, range.end), token: true });
    cursor = range.end;
  });
  if (cursor < value.length) parts.push({ text: value.slice(cursor), token: false });
  return parts;
}

/**
 * 光标落在词条内或紧邻词条时，Backspace/Delete 应一次删掉整个词条。
 * 顺带吞掉一个分隔空格，避免留下成对空白。
 */
export function tokenDeletionRange(
  value: string,
  tokens: readonly string[],
  caret: number,
  direction: 'backward' | 'forward',
): TokenRange | null {
  for (const range of tokenRanges(value, tokens)) {
    const touched = direction === 'backward'
      ? caret > range.start && caret <= range.end
      : caret >= range.start && caret < range.end;
    if (!touched) continue;
    const eatsTrailing = value[range.end] === ' ';
    const end = eatsTrailing ? range.end + 1 : range.end;
    const start = !eatsTrailing && range.start > 0 && value[range.start - 1] === ' ' ? range.start - 1 : range.start;
    return { start, end };
  }
  return null;
}
