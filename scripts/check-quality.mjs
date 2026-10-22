#!/usr/bin/env node
// 代码结构门禁：判定"依赖关系与晦涩度"，不规定文件该有多少行。
//
//   硬门禁（失败）：绝对禁令、循环依赖、跨层反向依赖
//   软信号（只报告）：规模与增长，用来触发复审，不参与通过/失败判定
//
//   node scripts/check-quality.mjs            检查
//   node scripts/check-quality.mjs --report   只报告，不判失败
//   node scripts/check-quality.mjs --update   用当前结构刷新已知债务清单（只收紧）
import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const PROJECT_ROOT = path.resolve(import.meta.dirname, '..');
const require = createRequire(path.join(PROJECT_ROOT, 'frontend/package.json'));
const ts = require('typescript');

export function checkQuality({ root = PROJECT_ROOT, mode = 'check', output = console.log } = {}) {
  const ROOT = root;
  const MODE = mode;
  const BASELINE_PATH = path.join(ROOT, 'scripts/quality-baseline.json');
  const toPosix = p => p.split(path.sep).join('/');

  const SKIP = new Set(['node_modules', '.git', '.runtime', 'dist', 'build', '__pycache__', '.venv',
    '.tools', '.uv-cache', '.pytest_cache', '.ruff_cache', '.codebase-memory', '.claude', '.codex']);

  function walk(dir, out = []) {
    if (!existsSync(dir)) return out;
    for (const name of readdirSync(dir)) {
      if (SKIP.has(name)) continue;
      const abs = path.join(dir, name);
      if (statSync(abs).isDirectory()) walk(abs, out);
      else out.push(toPosix(path.relative(ROOT, abs)));
    }
    return out;
  }

  const backendFiles = walk(path.join(ROOT, 'backend/src/short_drama'));
  const frontendFiles = walk(path.join(ROOT, 'frontend/src'));
  const allFiles = [...backendFiles, ...frontendFiles];

  // ---------- 1. 绝对禁令 ----------
  const BANS = [
    { rule: 'TS 忽略指令', exts: ['.ts', '.tsx'], dirs: ['frontend/src'], pattern: /@ts-(ignore|expect-error)/ },
    { rule: 'Python 忽略指令', exts: ['.py'], dirs: ['backend/src'], pattern: /#\s*type:\s*ignore/ },
    { rule: '遗留待办', exts: ['.py', '.ts', '.tsx'], dirs: ['backend/src', 'frontend/src'], pattern: /\b(TODO|FIXME)\b/ },
  ];

  const bans = [];
  for (const rel of allFiles) {
    const ext = path.extname(rel);
    const text = readFileSync(path.join(ROOT, rel), 'utf8');
    const lines = text.split('\n');
    for (const ban of BANS) {
      if (!ban.exts.includes(ext) || !ban.dirs.some(d => rel.startsWith(`${d}/`))) continue;
      lines.forEach((line, i) => {
        if (ban.pattern.test(line)) bans.push({ rule: ban.rule, file: rel, line: i + 1, detail: line.trim().slice(0, 90) });
      });
    }
  }

  // ---------- 2. 循环依赖 ----------
  function cyclesOf(edges, label) {
    let counter = 0;
    const stack = [], onStack = new Set(), index = new Map(), low = new Map(), found = [];
    const strong = v => {
      index.set(v, counter); low.set(v, counter); counter++;
      stack.push(v); onStack.add(v);
      for (const w of edges.get(v) ?? []) {
        if (!index.has(w)) { strong(w); low.set(v, Math.min(low.get(v), low.get(w))); }
        else if (onStack.has(w)) low.set(v, Math.min(low.get(v), index.get(w)));
      }
      if (low.get(v) === index.get(v)) {
        const comp = []; let w;
        do { w = stack.pop(); onStack.delete(w); comp.push(w); } while (w !== v);
        if (comp.length > 1 || (edges.get(v) ?? []).includes(v)) found.push(comp);
      }
    };
    for (const v of edges.keys()) if (!index.has(v)) strong(v);
    return found.map(comp => {
      const files = comp.map(p => toPosix(path.relative(ROOT, p))).sort();
      return { rule: `循环依赖（${label}）`, file: files[0], detail: files.join(' ↔ ') };
    });
  }

  const feTs = frontendFiles.filter(f => /\.(ts|tsx)$/.test(f));
  const feSet = new Set(feTs.map(f => path.join(ROOT, f)));
  const feEdges = new Map();
  const feImports = [];
  function isConsoleObject(node) {
    while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isTypeAssertionExpression(node) || ts.isNonNullExpression(node))) node = node.expression;
    if (!node) return false;
    if (ts.isIdentifier(node)) return node.text === 'console';
    if ((ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) && ts.isIdentifier(node.expression)
      && ['globalThis', 'window', 'self'].includes(node.expression.text)) {
      return ts.isPropertyAccessExpression(node) ? node.name.text === 'console'
        : ts.isStringLiteralLike(node.argumentExpression) && node.argumentExpression.text === 'console';
    }
    return false;
  }
  for (const rel of feTs) {
    const abs = path.join(ROOT, rel);
    const source = ts.createSourceFile(abs, readFileSync(abs, 'utf8'), ts.ScriptTarget.Latest, true);
    if (source.parseDiagnostics.length) {
      const diagnostic = source.parseDiagnostics[0];
      const line = source.getLineAndCharacterOfPosition(diagnostic.start ?? 0).line + 1;
      throw new Error(`TypeScript 解析失败：${rel}:${line} ${ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ')}`);
    }
    const deps = new Set();
    function visit(node) {
      const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
      if (node.kind === ts.SyntaxKind.AnyKeyword) {
        bans.push({ rule: 'TypeScript any', file: rel, line, detail: node.getText(source) });
      }
      if (((ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) && isConsoleObject(node.expression))
        || (ts.isVariableDeclaration(node) && isConsoleObject(node.initializer))
        || (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken && isConsoleObject(node.right))) {
        bans.push({ rule: '前端 console', file: rel, line, detail: node.getText(source) });
      }
      let specifier;
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
        specifier = node.moduleSpecifier;
      } else if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword
          || (ts.isIdentifier(node.expression) && node.expression.text === 'require'))) {
        specifier = node.arguments[0];
      } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) {
        specifier = node.argument.literal;
      }
      if (specifier && ts.isStringLiteralLike(specifier)) {
        if (/^axios(?:\/|$)/.test(specifier.text) && rel !== 'frontend/src/api/http.ts') {
          bans.push({ rule: 'axios 越界', file: rel, line, detail: '只允许 src/api/http.ts 引入 axios' });
        }
        const hit = ts.resolveModuleName(specifier.text, abs, { moduleResolution: ts.ModuleResolutionKind.Bundler }, ts.sys).resolvedModule?.resolvedFileName;
        if (hit && feSet.has(path.normalize(hit))) {
          deps.add(path.normalize(hit));
          feImports.push({ file: rel, target: toPosix(path.relative(ROOT, hit)), line });
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
    feEdges.set(abs, [...deps]);
  }

  const bePy = backendFiles.filter(f => f.endsWith('.py'));
  const python = path.join(PROJECT_ROOT, process.platform === 'win32' ? 'backend/.venv/Scripts/python.exe' : 'backend/.venv/bin/python');
  const parser = path.join(PROJECT_ROOT, 'scripts/check_quality_python.py');
  const result = spawnSync(existsSync(python) ? python : 'uv',
    [...(existsSync(python) ? [] : ['run', 'python']), parser, ROOT],
    { cwd: path.join(PROJECT_ROOT, 'backend'), encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
  if (result.error || result.status !== 0) {
    throw new Error(`Python 依赖解析失败：${result.error?.message ?? result.stderr}`);
  }
  const parsed = JSON.parse(result.stdout);
  bans.push(...parsed.bans);
  const beEdges = new Map(bePy.map(rel => [path.join(ROOT, rel), []]));
  for (const item of parsed.imports) {
    const abs = path.join(ROOT, item.file);
    if (!beEdges.has(abs)) beEdges.set(abs, []);
    if (item.target) beEdges.get(abs).push(path.join(ROOT, item.target));
  }

  const cycles = [...cyclesOf(feEdges, '前端'), ...cyclesOf(beEdges, '后端')];

  // ---------- 3. 跨层反向依赖 ----------
  const layers = [];
  const forbidden = { api: ['dao', 'domain'], dao: ['service', 'api'], domain: ['service', 'dao'], service: ['api'] };
  const layerKeys = new Set();
  for (const item of parsed.imports) {
    const from = item.file.split('/')[3];
    const to = item.module.split('.')[1];
    const key = `${item.file}:${item.line}:${to}`;
    if (forbidden[from]?.includes(to) && !layerKeys.has(key)) {
      layerKeys.add(key);
      layers.push({ rule: `${from} → ${to}`, file: item.file, line: item.line, detail: item.module });
    }
  }
  for (const item of feImports) {
    const from = item.file.split('/')[2];
    const to = item.target.split('/')[2];
    if ((from === 'api' && to === 'features') || (from === 'components' && to === 'api')) {
      layers.push({ rule: `${from} → ${to}`, file: item.file, line: item.line, detail: item.target });
    }
  }

  const structural = [...cycles, ...layers];

  // ---------- 4. 规模：只作为复审信号 ----------
  const SIGNAL = { lines: 600, maxClass: 500, maxFn: 100, params: 8, useState: 15 };

  const sizes = {};
  for (const rel of allFiles) {
    const lines = readFileSync(path.join(ROOT, rel), 'utf8').split('\n');
    let m;
    if (rel.endsWith('.py')) {
      m = parsed.metrics[rel];
      if (!m) throw new Error(`Python 规模解析缺失：${rel}`);
    } else if (/\.(ts|tsx)$/.test(rel)) {
      m = { lines: lines.length, useState: (lines.join('\n').match(/\buseState\s*[<(]/g) ?? []).length };
    } else continue;
    const over = m.lines > SIGNAL.lines || (m.maxClass ?? 0) > SIGNAL.maxClass
      || (m.maxFn ?? 0) > SIGNAL.maxFn || (m.params ?? 0) > SIGNAL.params || (m.useState ?? 0) > SIGNAL.useState;
    if (over) sizes[rel] = m;
  }

  // ---------- 汇总 ----------
  const baseline = existsSync(BASELINE_PATH)
    ? JSON.parse(readFileSync(BASELINE_PATH, 'utf8'))
    : { version: 2, structural: {}, sizes: {} };

  const keyOf = v => `${v.rule}::${v.file}`;
  const known = baseline.structural ?? {};
  const current = new Map();
  for (const v of structural) current.set(keyOf(v), (current.get(keyOf(v)) ?? 0) + 1);

  const fresh = [];
  const seen = new Map();
  for (const v of structural) {
    const key = keyOf(v);
    const count = (seen.get(key) ?? 0) + 1;
    seen.set(key, count);
    if (count > (known[key] ?? 0) || v.rule.startsWith('循环依赖')) fresh.push(v);
  }

  if (MODE === 'update' && !bans.length && !fresh.length) {
    const next = {};
    for (const [key, count] of current) next[key] = count;
    writeFileSync(BASELINE_PATH, `${JSON.stringify({ version: 2, structural: next, sizes }, null, 2)}\n`, 'utf8');
    output(`已知结构问题已更新：${Object.keys(next).length} 条；规模信号 ${Object.keys(sizes).length} 个文件。`);
    return { status: 0, bans, structural };
  }

  const grown = [];
  for (const [rel, m] of Object.entries(sizes)) {
    const before = baseline.sizes?.[rel];
    if (before && m.lines > before.lines) grown.push({ rel, from: before.lines, to: m.lines });
  }

  if (bans.length) {
    output(`\n绝对禁令 ${bans.length} 处`);
    for (const b of bans.slice(0, 25)) output(`  ${b.file}${b.line ? `:${b.line}` : ''}  ${b.rule}  ${b.detail ?? ''}`);
    if (bans.length > 25) output(`  … 另有 ${bans.length - 25} 处`);
  }

  if (MODE === 'report') {
    output('规模信号（用于复审，不阻塞）：');
    for (const [rel, m] of Object.entries(sizes).sort((a, b) => b[1].lines - a[1].lines).slice(0, 12)) {
      output(`  ${String(m.lines).padStart(5)} 行  ${rel}`);
    }
    for (const item of structural) output(`  ${item.rule}  ${item.detail}`);
    output(`\n共 ${Object.keys(sizes).length} 个文件超过审视线；绝对禁令 ${bans.length} 处；结构问题 ${structural.length} 处。`);
    return { status: 0, bans, structural };
  }

  if (fresh.length) {
    output(`\n新增结构问题 ${fresh.length} 处`);
    for (const v of fresh.slice(0, 25)) output(`  ${v.file}${v.line ? `:${v.line}` : ''}  ${v.rule}`);
    if (fresh.length > 25) output(`  … 另有 ${fresh.length - 25} 处`);
    output('  请修正依赖方向；--update 不接受新增问题或增加计数，循环依赖不可加入基线。');
  }

  const resolved = Object.keys(known).filter(key => !current.has(key) || current.get(key) < known[key]);
  if (resolved.length) output(`\n有 ${resolved.length} 处历史结构问题已消除，可运行 --update 收紧基线。`);
  if (grown.length) {
    output(`\n规模增长 ${grown.length} 处（提示复审，不阻塞）`);
    for (const g of grown.slice(0, 5)) output(`  ${g.rel}  ${g.from} → ${g.to} 行`);
  }

  const failed = bans.length > 0 || fresh.length > 0;
  if (!failed) {
    output(`\n结构门禁通过：${allFiles.length} 个源文件，${structural.length} 处已知结构问题，`
      + `${Object.keys(sizes).length} 个规模信号待复审。`);
  }
  return { status: failed ? 1 : 0, bans, structural };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const mode = process.argv.includes('--update') ? 'update' : process.argv.includes('--report') ? 'report' : 'check';
  process.exitCode = checkQuality({ mode }).status;
}
