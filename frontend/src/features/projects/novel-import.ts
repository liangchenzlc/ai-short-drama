export function decodeNovelFile(bytes: ArrayBuffer): string {
  if (bytes.byteLength > 1048576) throw new Error('TXT 文件不能超过 1 MiB。');
  let content: string;
  try { content = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { content = new TextDecoder('gb18030', { fatal: true }).decode(bytes); }
  content = content.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
  if (!content.trim() || content.includes('\0')) throw new Error('文件没有可导入的正文，请选择有效的 TXT 文件。');
  if (new TextEncoder().encode(content).length > 1048576) throw new Error('转为 UTF-8 后正文超过 1 MiB，请拆分文件。');
  return content;
}
