#!/usr/bin/env node
// 文档与代码一致性检查：代码变更后跑一遍，避免维护文档长期漂移。
// 用法：node scripts/check-docs.mjs [--quiet]
// 退出码：0 一致；1 存在不一致（逐条列出文件与行号）。
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { collectApiModules, compareApiModuleInventory } from './docs-api-modules.mjs';

const ROOT = path.resolve(import.meta.dirname, '..');
const quiet = process.argv.includes('--quiet');
const toPosix = p => p.split(path.sep).join('/');
const problems = [];
const add = (file, line, message) => problems.push({ file, line, message });

const SKIP_DIRS = new Set([
  'node_modules', '.git', '.runtime', 'dist', 'build', '__pycache__', '.codebase-memory',
  '.workbuddy', '.claude', '.codex', '.cursor', '.venv', '.impeccable', '.playwright-cli',
  '.tools', '.uv-cache', '.pytest_cache', '.ruff_cache',
]);

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const abs = path.join(dir, name);
    const st = statSync(abs);
    if (st.isDirectory()) walk(abs, out);
    else out.push(toPosix(path.relative(ROOT, abs)));
  }
  return out;
}

const allFiles = walk(ROOT);
const byBasename = new Map();
for (const rel of allFiles) {
  const base = path.basename(rel);
  if (!byBasename.has(base)) byBasename.set(base, rel);
}

// ---------- 待检查的维护文档 ----------
const docs = [
  'AGENTS.md',
  'frontend/AGENTS.md',
  'backend/AGENTS.md',
  'PRINCIPLES.md',
  'README.md',
  ...readdirSync(path.join(ROOT, 'docs/instruction'))
    .filter(f => f.endsWith('.md') && f !== '测试文件清单.md')
    .map(f => `docs/instruction/${f}`),
  ...readdirSync(path.join(ROOT, 'docs/api')).filter(f => f.endsWith('.md')).map(f => `docs/api/${f}`),
  'docs/database/README.md',
  'docs/database/migrations/README.md',
];

// ---------- 事实来源 ----------
const schemaSql = readFileSync(path.join(ROOT, 'docs/database/schema/schema.mysql8.sql'), 'utf8');
const tableCount = (schemaSql.match(/^CREATE TABLE /gm) ?? []).length;

const apiModules = collectApiModules(path.join(ROOT, 'backend/src/short_drama/api/v1'));

const npmScripts = new Set();
for (const pkg of ['frontend/package.json', 'frontend/canvas/package.json']) {
  const abs = path.join(ROOT, pkg);
  if (existsSync(abs)) Object.keys(JSON.parse(readFileSync(abs, 'utf8')).scripts ?? {}).forEach(s => npmScripts.add(s));
}

const startScript = path.join(ROOT, 'backend/scripts/start_generation.ps1');
const roles = new Set();
if (existsSync(startScript)) {
  const m = readFileSync(startScript, 'utf8').match(/ValidateSet\(([^)]*)\)/);
  if (m) for (const r of m[1].matchAll(/'([^']+)'/g)) roles.add(r[1]);
}


// ---------- 路径可解析性 ----------
function resolves(docRel, token) {
  const t = token.replace(/\\/g, '/').replace(/^\.\//, '');
  if (!t || /[*{}<>]/.test(t)) return true;                 // 通配或占位，跳过
  if (t.startsWith('http') || t.startsWith('#')) return true;
  const docDir = path.dirname(path.join(ROOT, docRel));
  if (existsSync(path.resolve(docDir, t))) return true;
  const prefixed = [
    t,
    `backend/${t}`,
    `backend/src/short_drama/${t}`,
    `backend/scripts/${t}`,
    `frontend/${t}`,
    `frontend/src/${t}`,
    `frontend/canvas/src/${t}`,
  ];
  if (prefixed.some(p => existsSync(path.join(ROOT, p)))) return true;
  return byBasename.has(path.basename(t));                  // 同名文件存在即视为未漂移
}

const PATH_EXT = /\.(?:py|ts|tsx|js|mjs|cjs|md|sql|json|ya?ml|ps1|sh|html|css)$/i;

for (const docRel of docs) {
  const lines = readFileSync(path.join(ROOT, docRel), 'utf8').split('\n');
  lines.forEach((line, index) => {
    const lineNo = index + 1;

    for (const m of line.matchAll(/\]\(([^()\s]+)\)/g)) {
      const target = decodeURIComponent(m[1].split('#')[0]);
      if (!target || /^(?:[a-z]+:|\/\/|#)/i.test(m[1])) continue;
      if (!resolves(docRel, target)) add(docRel, lineNo, `链接失效：${m[1]}`);
    }

    for (const m of line.matchAll(/`([^`\n]+)`/g)) {
      const token = m[1].trim();
      if (!token || /\s/.test(token) || token.includes('://') || token.startsWith('/')) continue;
      if (!PATH_EXT.test(token)) continue;
      if (!token.includes('/') && !token.includes('\\')) continue;
      if (!resolves(docRel, token)) add(docRel, lineNo, `路径不存在：${token}`);
    }

    // 表数量断言：只检查"当前口径"的句子，历史阶段与迁移台账里的数字是事实记录，不参与比对
    const isLedger = docRel === 'docs/database/migrations/README.md';
    const historical = /(当时|仍为|此前|升级前|原有|历史)[^。]{0,10}\d+\s*张表/.test(line);
    const currentClaim = /(当前|目前|现在|完整|全量|创建)[^。]{0,20}\d+\s*张表/.test(line)
      || /^\|\s*MySQL\s*\|.*\d+\s*张表/.test(line);
    if (!isLedger && !historical && currentClaim) {
      for (const m of line.matchAll(/(\d+)\s*张表/g)) {
        if (Number(m[1]) !== tableCount) add(docRel, lineNo, `表数量写的是 ${m[1]}，实际 ${tableCount}`);
      }
    }

    // npm 脚本
    for (const m of line.matchAll(/npm run ([a-z][a-z0-9:-]*)/gi)) {
      if (!npmScripts.has(m[1])) add(docRel, lineNo, `npm 脚本不存在：${m[1]}`);
    }

    // 启动脚本角色
    for (const m of line.matchAll(/-Role\s+([A-Za-z]+)/g)) {
      if (roles.size && !roles.has(m[1])) add(docRel, lineNo, `启动角色不存在：${m[1]}`);
    }
  });
}

// ---------- 接口模块清单 ----------
const overviewPath = path.join(ROOT, 'docs/instruction/项目概览.md');
if (existsSync(overviewPath)) {
  const lines = readFileSync(overviewPath, 'utf8').split('\n');
  for (const { line, message } of compareApiModuleInventory(lines, apiModules)) {
    add('docs/instruction/项目概览.md', line, message);
  }
}

// ---------- 输出 ----------
if (!problems.length) {
  console.log(`文档与代码一致：${docs.length} 份维护文档，${tableCount} 张表，${apiModules.length} 个路由模块。`);
  process.exit(0);
}

const grouped = new Map();
for (const p of problems) {
  if (!grouped.has(p.file)) grouped.set(p.file, []);
  grouped.get(p.file).push(p);
}
for (const [file, list] of grouped) {
  console.log(`\n${file}`);
  for (const item of list) console.log(`  ${item.line}: ${item.message}`);
}
console.log(`\n共 ${problems.length} 处不一致。改代码后同步对应文档，再重跑 node scripts/check-docs.mjs。`);
process.exit(1);
