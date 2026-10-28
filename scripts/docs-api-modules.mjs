import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

// 按真正声明 HTTP 操作的文件登记，包入口与纯聚合 router 不重复计数。
export function collectApiModules(directory, prefix = '') {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const relative = prefix + entry.name;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) return entry.name === '__pycache__' ? [] : collectApiModules(absolute, relative + '/');
    if (!entry.name.endsWith('.py') || relative === 'test.py') return [];
    const source = readFileSync(absolute, 'utf8');
    return /^\s*@router\.(?:get|post|put|patch|delete|options|head|api_route)\s*\(/m.test(source)
      ? [relative.replace(/\.py$/, '')] : [];
  }).sort();
}

export function compareApiModuleInventory(lines, actual) {
  const start = lines.findIndex(line => line.startsWith('### 接口分组'));
  if (start < 0) return [{ line: 1, message: '缺少接口分组清单' }];
  const end = lines.findIndex((line, index) => index > start && /^#{1,3}\s/.test(line));
  const block = lines.slice(start + 1, end < 0 ? undefined : end).join('\n');
  const listed = new Set([...block.matchAll(/`([a-z][a-z0-9_]*(?:\/[a-z][a-z0-9_]*)*)`/g)].map(match => match[1]));
  const current = new Set(actual);
  return [
    ...actual.filter(name => !listed.has(name)).map(name => `接口模块未登记：${name}`),
    ...[...listed].filter(name => !current.has(name)).map(name => `接口模块已不存在：${name}`),
  ].map(message => ({ line: start + 2, message }));
}
